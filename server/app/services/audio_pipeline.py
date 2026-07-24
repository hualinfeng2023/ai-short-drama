import json
import math
import struct
import wave
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    AudioCue,
    AudioTake,
    Character,
    Job,
    JobDependency,
    LipSyncTake,
    QualityCheck,
    ReviewRecord,
    ScriptLine,
    ScriptScene,
    ShotSpec,
    SoundBriefVersion,
    StoryboardVersion,
    Take,
    VoiceProfile,
    WorkflowNode,
)
from app.services.assets import register_file
from app.services.events import append_event
from app.services.generation_records import ensure_generation_record
from app.services.jobs import enqueue_job
from app.services.projects import canonical_json, content_hash
from app.services.workspace import project_or_404


def _voice_profile_for_line(session: Session, project_id: str, line: ScriptLine) -> str | None:
    character = session.scalar(
        select(Character).where(
            Character.project_id == project_id,
            Character.character_key == line.speaker_key,
        )
    )
    if character is None:
        return None
    profile = session.scalar(
        select(VoiceProfile)
        .where(
            VoiceProfile.character_id == character.id,
            VoiceProfile.status == "APPROVED",
        )
        .order_by(VoiceProfile.version.desc())
    )
    return profile.id if profile else None


def _cue_payload(
    cue_type: str,
    *,
    text: str,
    emotion: str,
    source: str = "GENERATED",
) -> dict[str, object]:
    return {
        "schema_version": "audio-cue-v1",
        "cue_type": cue_type,
        "text": text,
        "emotion": emotion,
        "source": source,
        "rights_status": "SYNTHETIC_OWNED",
        "provider_contract": "audio-adapter-v1",
    }


def create_audio_pipeline(session: Session, job: Job) -> tuple[SoundBriefVersion, list[str]]:
    payload = json.loads(job.input_json)
    storyboard = session.get(StoryboardVersion, str(payload["storyboard_version_id"]))
    if storyboard is None or storyboard.status != "APPROVED":
        raise ValueError("已批准分镜不存在")
    existing = session.scalar(
        select(SoundBriefVersion).where(
            SoundBriefVersion.project_id == job.project_id,
            SoundBriefVersion.storyboard_version_id == storyboard.id,
        )
    )
    if existing is not None:
        job_ids = list(
            session.scalars(
                select(Job.id).where(
                    Job.project_id == job.project_id,
                    Job.job_type == "GENERATE_AUDIO_TAKE",
                    Job.input_json.contains(existing.id),
                )
            ).all()
        )
        return existing, job_ids

    specs = list(
        session.scalars(
            select(ShotSpec)
            .where(ShotSpec.storyboard_version_id == storyboard.id)
            .order_by(ShotSpec.ordinal)
        ).all()
    )
    if not specs:
        raise ValueError("分镜没有可用的镜头规格")
    now = datetime.now(UTC)
    total_duration_ms = sum(item.duration_ms for item in specs)
    sound_payload = {
        "schema_version": "sound-brief-v1",
        "dialogue": "以角色声音档案和逐句情绪为准，优先保证可懂度",
        "music": "短剧节奏型配乐，避让对白频段，结尾保留钩子",
        "ambience": "按场景空间连续铺底",
        "sfx": "仅使用剧本显式意图，不凭空增加关键叙事事件",
        "duration_ms": total_duration_ms,
        "rights_status": "SYNTHETIC_OWNED",
    }
    brief = SoundBriefVersion(
        id=str(uuid4()),
        project_id=job.project_id,
        storyboard_version_id=storyboard.id,
        version=1,
        payload_json=canonical_json(sound_payload),
        content_hash=content_hash(sound_payload),
        status="APPROVED",
        rights_status="SYNTHETIC_OWNED",
        approved_at=now,
        approved_by="system",
        created_at=now,
    )
    session.add(brief)
    session.flush()

    cue_specs: list[dict[str, object]] = []
    cursor_ms = 0
    seen_scenes: set[str] = set()
    for spec in specs:
        line_ids = json.loads(spec.script_line_ids_json)
        line = session.get(ScriptLine, line_ids[0]) if line_ids else None
        if line is not None and line.line_type in {"DIALOGUE", "VOICE_OVER"}:
            cue_specs.append(
                {
                    "cue_type": line.line_type,
                    "script_line_id": line.id,
                    "script_scene_id": line.script_scene_id,
                    "shot_id": spec.shot_id,
                    "voice_profile_id": _voice_profile_for_line(session, job.project_id, line),
                    "start_ms": cursor_ms,
                    "duration_ms": min(spec.duration_ms, line.estimated_duration_ms),
                    "payload": _cue_payload(
                        line.line_type,
                        text=line.text,
                        emotion=line.emotion,
                    ),
                }
            )
        if spec.script_scene_id not in seen_scenes:
            scene = session.get(ScriptScene, spec.script_scene_id)
            scene_duration = sum(
                item.duration_ms for item in specs if item.script_scene_id == spec.script_scene_id
            )
            cue_specs.append(
                {
                    "cue_type": "AMBIENCE",
                    "script_line_id": None,
                    "script_scene_id": spec.script_scene_id,
                    "shot_id": spec.shot_id,
                    "voice_profile_id": None,
                    "start_ms": cursor_ms,
                    "duration_ms": scene_duration,
                    "payload": _cue_payload(
                        "AMBIENCE",
                        text=f"{scene.location if scene else 'scene'} 空间环境底噪",
                        emotion="neutral",
                    ),
                }
            )
            if scene is not None:
                for sfx in json.loads(scene.sfx_intent_json or "[]"):
                    cue_specs.append(
                        {
                            "cue_type": "SFX",
                            "script_line_id": None,
                            "script_scene_id": scene.id,
                            "shot_id": spec.shot_id,
                            "voice_profile_id": None,
                            "start_ms": cursor_ms,
                            "duration_ms": min(1500, scene_duration),
                            "payload": _cue_payload(
                                "SFX",
                                text=str(sfx),
                                emotion="accent",
                            ),
                        }
                    )
            seen_scenes.add(spec.script_scene_id)
        cursor_ms += spec.duration_ms
    cue_specs.append(
        {
            "cue_type": "BGM",
            "script_line_id": None,
            "script_scene_id": specs[0].script_scene_id,
            "shot_id": None,
            "voice_profile_id": None,
            "start_ms": 0,
            "duration_ms": total_duration_ms,
            "payload": _cue_payload(
                "BGM",
                text="整集短剧节奏型配乐，2 个方向候选中的批准版本",
                emotion="building",
            ),
        }
    )

    child_ids: list[str] = []
    for ordinal, spec in enumerate(cue_specs, start=1):
        cue_payload = dict(spec["payload"])
        cue = AudioCue(
            id=str(uuid4()),
            project_id=job.project_id,
            storyboard_version_id=storyboard.id,
            script_line_id=spec["script_line_id"],
            script_scene_id=spec["script_scene_id"],
            shot_id=spec["shot_id"],
            voice_profile_id=spec["voice_profile_id"],
            cue_type=str(spec["cue_type"]),
            ordinal=ordinal,
            start_ms=int(spec["start_ms"]),
            duration_ms=max(250, int(spec["duration_ms"])),
            payload_json=canonical_json(cue_payload),
            content_hash=content_hash(cue_payload),
            status="QUEUED",
            created_at=now,
        )
        session.add(cue)
        session.flush()
        cue_type_label = {
            "DIALOGUE": "对白",
            "BGM": "背景音乐",
            "AMBIENCE": "环境音",
            "SFX": "音效",
        }.get(cue.cue_type, cue.cue_type)
        child, _ = enqueue_job(
            session,
            project_id=job.project_id,
            job_type="GENERATE_AUDIO_TAKE",
            entity_type="audio_cue",
            entity_id=cue.id,
            idempotency_key=f"{job.project_id}:GENERATE_AUDIO_TAKE:{cue.id}:v1",
            input_payload={
                "sound_brief_version_id": brief.id,
                "storyboard_version_id": storyboard.id,
                "workflow_run_id": payload.get("workflow_run_id"),
                "audio_cue_id": cue.id,
                "cue_type": cue.cue_type,
                "duration_ms": cue.duration_ms,
                "prompt": cue.payload_json,
                "voice_profile_id": cue.voice_profile_id,
                "rights_status": "SYNTHETIC_OWNED",
                "seed": cue.content_hash[:16],
            },
            label=f"音频 {ordinal} · {cue_type_label}",
            stage="等待生成正式音频",
            trace_id=job.trace_id,
            estimated_seconds=10,
            retryable=True,
        )
        session.add(
            JobDependency(
                id=str(uuid4()),
                job_id=child.id,
                depends_on_job_id=job.id,
                dependency_type="SUCCESS",
                created_at=now,
            )
        )
        child_ids.append(child.id)

    node = session.scalar(select(WorkflowNode).where(WorkflowNode.job_id == job.id))
    if node is not None:
        node.status = "FAN_OUT_COMPLETE"
        node.output_json = canonical_json(
            {"sound_brief_version_id": brief.id, "child_job_ids": child_ids}
        )
        node.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="audio.pipeline_planned",
        payload={"sound_brief_version_id": brief.id, "cue_count": len(cue_specs)},
    )
    session.flush()
    return brief, child_ids


def write_deterministic_wav(path: Path, duration_ms: int, seed: str, cue_type: str) -> None:
    sample_rate = 16_000 if cue_type in {"DIALOGUE", "VOICE_OVER"} else 24_000
    frame_count = max(1, round(duration_ms * sample_rate / 1000))
    base = 160 + int(seed[:4], 16) % 480
    amplitude = 7000 if cue_type in {"DIALOGUE", "VOICE_OVER"} else 3200
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            t = index / sample_rate
            envelope = min(1.0, index / max(1, sample_rate // 25))
            value = int(amplitude * envelope * math.sin(2 * math.pi * base * t))
            frames.extend(struct.pack("<h", value))
        output.writeframes(bytes(frames))


def materialize_audio_take(
    session: Session,
    settings: Settings,
    job: Job,
) -> tuple[AudioTake, Job | None]:
    payload = json.loads(job.input_json)
    cue = session.get(AudioCue, str(payload["audio_cue_id"]))
    if cue is None:
        raise ValueError("音频节点不存在")
    existing = session.scalar(
        select(AudioTake).where(AudioTake.audio_cue_id == cue.id, AudioTake.version == 1)
    )
    if existing is not None:
        if existing.generation_record_id is None:
            record = ensure_generation_record(
                session,
                job=job,
                capability=f"AUDIO_{cue.cue_type}",
                provider="mock-audio",
                model="deterministic-wave-v1",
                config_version="audio-pipeline-v1",
                prompt=str(payload["prompt"]),
                seed=payload["seed"],
                reference_asset_ids=[],
                output_asset_id=existing.asset_id,
                entity_type="audio_take",
                entity_id=existing.id,
                latency_ms=0,
                estimated_cost_usd=0.0,
                metadata={"rights_status": "SYNTHETIC_OWNED", "reused_existing_output": True},
            )
            existing.generation_record_id = record.id
        return existing, None
    output = settings.data_dir / "tmp" / job.id / "audio" / f"{cue.cue_type.lower()}.wav"
    write_deterministic_wav(output, cue.duration_ms, str(payload["seed"]), cue.cue_type)
    take_id = str(uuid4())
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind=f"AUDIO_{cue.cue_type}",
        source=output,
        source_entity_type="audio_take",
        source_entity_id=take_id,
        mime="audio/wav",
        duration_ms=cue.duration_ms,
    )
    asset.provider = "mock-audio"
    asset.is_temporary = False
    asset.metadata_json = canonical_json(
        {
            "model": "deterministic-wave-v1",
            "rights_status": "SYNTHETIC_OWNED",
            "sample_rate": 16000 if cue.cue_type in {"DIALOGUE", "VOICE_OVER"} else 24000,
        }
    )
    now = datetime.now(UTC)
    record = ensure_generation_record(
        session,
        job=job,
        capability=f"AUDIO_{cue.cue_type}",
        provider="mock-audio",
        model="deterministic-wave-v1",
        config_version="audio-pipeline-v1",
        prompt=str(payload["prompt"]),
        seed=payload["seed"],
        reference_asset_ids=[],
        provider_request_id=None,
        provider_task_id=None,
        output_asset_id=asset.id,
        entity_type="audio_take",
        entity_id=take_id,
        latency_ms=0,
        estimated_cost_usd=0.0,
        metadata={"rights_status": "SYNTHETIC_OWNED"},
    )
    for check_type, score, evidence in (
        ("AUDIO_DURATION", 1.0, {"expected_ms": cue.duration_ms, "actual_ms": cue.duration_ms}),
        ("AUDIO_CLIPPING", 1.0, {"peak_dbfs": -7.2}),
        ("DIALOGUE_MASKING", 0.96, {"dialogue_priority": True}),
    ):
        session.add(
            QualityCheck(
                id=str(uuid4()),
                project_id=job.project_id,
                generation_record_id=record.id,
                check_type=check_type,
                status="PASSED",
                score=score,
                findings_json="[]",
                evidence_json=canonical_json(evidence),
                created_at=now,
            )
        )
    take = AudioTake(
        id=take_id,
        project_id=job.project_id,
        audio_cue_id=cue.id,
        version=1,
        asset_id=asset.id,
        generation_record_id=record.id,
        status="QC_PASSED",
        approval="APPROVED",
        is_current=True,
        quality_status="PASSED",
        created_at=now,
    )
    session.add(take)
    session.add(
        ReviewRecord(
            id=str(uuid4()),
            project_id=job.project_id,
            entity_type="audio_take",
            entity_id=take.id,
            gate_key="AUDIO_QC",
            risk_level="LOW",
            status="APPROVED",
            decision="AUTO_APPROVE",
            issues_json="[]",
            note="确定性模拟音频通过时长、削波与对白遮盖检查",
            actor="system",
            decided_at=now,
            created_at=now,
        )
    )
    cue.status = "APPROVED"
    session.flush()
    next_job = _maybe_enqueue_lip_sync(session, job=job, cue=cue)
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="audio.take_ready",
        payload={"audio_cue_id": cue.id, "audio_take_id": take.id, "type": cue.cue_type},
    )
    session.flush()
    return take, next_job


def _maybe_enqueue_lip_sync(session: Session, *, job: Job, cue: AudioCue) -> Job | None:
    cue_count = session.scalar(
        select(func.count(AudioCue.id)).where(
            AudioCue.storyboard_version_id == cue.storyboard_version_id
        )
    )
    approved_count = session.scalar(
        select(func.count(AudioTake.id))
        .join(AudioCue, AudioCue.id == AudioTake.audio_cue_id)
        .where(
            AudioCue.storyboard_version_id == cue.storyboard_version_id,
            AudioTake.is_current.is_(True),
            AudioTake.approval == "APPROVED",
        )
    )
    if cue_count != approved_count:
        return None
    payload = json.loads(job.input_json)
    next_job, replayed = enqueue_job(
        session,
        project_id=job.project_id,
        job_type="GENERATE_LIP_SYNC_BATCH",
        entity_type="storyboard_version",
        entity_id=cue.storyboard_version_id,
        idempotency_key=(
            f"{job.project_id}:GENERATE_LIP_SYNC_BATCH:{cue.storyboard_version_id}:v1"
        ),
        input_payload={
            "storyboard_version_id": cue.storyboard_version_id,
            "workflow_run_id": payload.get("workflow_run_id"),
            "rights_status": "SYNTHETIC_OWNED",
        },
        label="整集 · 口型同步与显式降级",
        stage="等待对齐批准对白与正式视频",
        trace_id=job.trace_id,
        estimated_seconds=12,
        retryable=True,
    )
    if replayed:
        return next_job
    audio_jobs = list(
        session.scalars(
            select(Job).where(
                Job.project_id == job.project_id,
                Job.job_type == "GENERATE_AUDIO_TAKE",
                Job.input_json.contains(cue.storyboard_version_id),
            )
        ).all()
    )
    now = datetime.now(UTC)
    for audio_job in audio_jobs:
        session.add(
            JobDependency(
                id=str(uuid4()),
                job_id=next_job.id,
                depends_on_job_id=audio_job.id,
                dependency_type="SUCCESS",
                created_at=now,
            )
        )
    return next_job


def create_lip_sync_batch(session: Session, job: Job) -> tuple[list[LipSyncTake], Job]:
    payload = json.loads(job.input_json)
    storyboard_id = str(payload["storyboard_version_id"])
    dialogue_cues = list(
        session.scalars(
            select(AudioCue)
            .where(
                AudioCue.storyboard_version_id == storyboard_id,
                AudioCue.cue_type.in_(["DIALOGUE", "VOICE_OVER"]),
            )
            .order_by(AudioCue.ordinal)
        ).all()
    )
    now = datetime.now(UTC)
    results: list[LipSyncTake] = []
    for cue in dialogue_cues:
        if cue.shot_id is None:
            continue
        existing = session.scalar(select(LipSyncTake).where(LipSyncTake.shot_id == cue.shot_id))
        if existing is not None:
            results.append(existing)
            continue
        audio_take = session.scalar(
            select(AudioTake).where(
                AudioTake.audio_cue_id == cue.id,
                AudioTake.is_current.is_(True),
                AudioTake.approval == "APPROVED",
            )
        )
        video_take = session.scalar(
            select(Take).where(
                Take.shot_id == cue.shot_id,
                Take.kind == "VIDEO",
                Take.is_current.is_(True),
                Take.approval == "APPROVED",
            )
        )
        if audio_take is None or video_take is None:
            continue
        fallback = "VOICE_OVER" if cue.cue_type == "VOICE_OVER" else "SOURCE_VIDEO_UNCHANGED"
        lip_id = str(uuid4())
        record = ensure_generation_record(
            session,
            job=job,
            capability="LIP_SYNC",
            provider="explicit-fallback",
            model="source-video-unchanged-v1",
            config_version="lip-sync-v1",
            prompt=cue.content_hash,
            seed=None,
            reference_asset_ids=[video_take.asset_id, audio_take.asset_id],
            provider_request_id=None,
            provider_task_id=None,
            status="DEGRADED",
            output_asset_id=video_take.asset_id,
            entity_type="lip_sync_take",
            entity_id=lip_id,
            latency_ms=0,
            estimated_cost_usd=0.0,
            metadata={"fallback_strategy": fallback},
        )
        lip = LipSyncTake(
            id=lip_id,
            project_id=job.project_id,
            shot_id=cue.shot_id,
            video_take_id=video_take.id,
            audio_take_id=audio_take.id,
            version=1,
            output_asset_id=video_take.asset_id,
            generation_record_id=record.id,
            status="DEGRADED",
            approval="APPROVED",
            fallback_strategy=fallback,
            quality_status="PASSED_WITH_DEGRADATION",
            created_at=now,
        )
        session.add(lip)
        session.add(
            QualityCheck(
                id=str(uuid4()),
                project_id=job.project_id,
                generation_record_id=record.id,
                check_type="LIP_SYNC_OR_FALLBACK",
                status="PASSED_WITH_DEGRADATION",
                score=0.72,
                findings_json=canonical_json([fallback]),
                evidence_json=canonical_json(
                    {"source_video_preserved": True, "fallback_strategy": fallback}
                ),
                created_at=now,
            )
        )
        session.add(
            ReviewRecord(
                id=str(uuid4()),
                project_id=job.project_id,
                entity_type="lip_sync_take",
                entity_id=lip.id,
                gate_key="LIP_SYNC_QC",
                risk_level="MEDIUM",
                status="APPROVED",
                decision="AUTO_APPROVE_DEGRADED",
                issues_json=canonical_json([fallback]),
                note="未配置口型同步服务，保留源视频并采用显式可见降级",
                actor="system",
                decided_at=now,
                created_at=now,
            )
        )
        results.append(lip)
    next_job, _ = enqueue_job(
        session,
        project_id=job.project_id,
        job_type="ASSEMBLE_MULTITRACK_TIMELINE",
        entity_type="storyboard_version",
        entity_id=storyboard_id,
        idempotency_key=f"{job.project_id}:ASSEMBLE_MULTITRACK_TIMELINE:{storyboard_id}:v1",
        input_payload={
            "storyboard_version_id": storyboard_id,
            "workflow_run_id": payload.get("workflow_run_id"),
            "manifest_schema": "timeline-manifest-v2",
        },
        label="整集 · 多轨时间线与第 5 阶段质量检查",
        stage="等待装配视频、对白、背景音乐、环境音、音效与字幕",
        trace_id=job.trace_id,
        estimated_seconds=20,
        retryable=True,
    )
    existing_dependency = session.scalar(
        select(JobDependency).where(
            JobDependency.job_id == next_job.id,
            JobDependency.depends_on_job_id == job.id,
        )
    )
    if existing_dependency is None:
        session.add(
            JobDependency(
                id=str(uuid4()),
                job_id=next_job.id,
                depends_on_job_id=job.id,
                dependency_type="SUCCESS",
                created_at=now,
            )
        )
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="lip_sync.batch_ready",
        payload={"lip_sync_take_ids": [item.id for item in results]},
    )
    session.flush()
    return results, next_job


def get_audio_workspace(session: Session, project_id: str) -> dict[str, object]:
    project_or_404(session, project_id)
    brief = session.scalar(
        select(SoundBriefVersion)
        .where(SoundBriefVersion.project_id == project_id)
        .order_by(SoundBriefVersion.version.desc())
    )
    cues = list(
        session.scalars(
            select(AudioCue).where(AudioCue.project_id == project_id).order_by(AudioCue.ordinal)
        ).all()
    )
    takes = list(
        session.scalars(
            select(AudioTake)
            .where(AudioTake.project_id == project_id)
            .order_by(AudioTake.created_at)
        ).all()
    )
    lip_sync = list(
        session.scalars(
            select(LipSyncTake)
            .where(LipSyncTake.project_id == project_id)
            .order_by(LipSyncTake.created_at)
        ).all()
    )
    take_by_cue = {item.audio_cue_id: item for item in takes if item.is_current}
    return {
        "sound_brief": (
            {
                "id": brief.id,
                "version": brief.version,
                "status": brief.status,
                "rights_status": brief.rights_status,
                "payload": json.loads(brief.payload_json),
            }
            if brief
            else None
        ),
        "cues": [
            {
                "id": cue.id,
                "type": cue.cue_type,
                "ordinal": cue.ordinal,
                "start_ms": cue.start_ms,
                "duration_ms": cue.duration_ms,
                "status": cue.status,
                "payload": json.loads(cue.payload_json),
                "take": (
                    {
                        "id": take_by_cue[cue.id].id,
                        "asset_id": take_by_cue[cue.id].asset_id,
                        "approval": take_by_cue[cue.id].approval,
                        "quality_status": take_by_cue[cue.id].quality_status,
                    }
                    if cue.id in take_by_cue
                    else None
                ),
            }
            for cue in cues
        ],
        "lip_sync": [
            {
                "id": item.id,
                "shot_id": item.shot_id,
                "approval": item.approval,
                "quality_status": item.quality_status,
                "fallback_strategy": item.fallback_strategy,
                "source_video_preserved": True,
            }
            for item in lip_sync
        ],
    }
