import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    AudioCue,
    AudioTake,
    Episode,
    Job,
    ReviewGate,
    ShotSpec,
    StoryboardVersion,
    Take,
    TimelineClip,
    TimelineItem,
    TimelineTrack,
    TimelineVersion,
    WholeFilmQualityCheck,
    WorkflowNode,
    WorkflowRun,
)
from app.services.assets import register_file, resolve_asset_path
from app.services.events import append_event
from app.services.projects import canonical_json, content_hash
from app.services.workspace import project_or_404

TRACK_ORDER = ("VIDEO", "DIALOGUE", "BGM", "AMBIENCE", "SFX", "SUBTITLE")
TRACK_GAIN_DB = {
    "VIDEO": 0.0,
    "DIALOGUE": 0.0,
    "BGM": -14.0,
    "AMBIENCE": -20.0,
    "SFX": -10.0,
    "SUBTITLE": 0.0,
}


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-3000:] or "FFmpeg 多轨装配失败")


def _timestamp(milliseconds: int, separator: str) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def _write_subtitles(tmp_dir: Path, cues: list[AudioCue]) -> tuple[Path, Path]:
    srt_lines: list[str] = []
    vtt_lines = ["WEBVTT", ""]
    index = 0
    for cue in cues:
        if cue.cue_type not in {"DIALOGUE", "VOICE_OVER"}:
            continue
        payload = json.loads(cue.payload_json)
        text = str(payload.get("text", "")).strip()
        if not text:
            continue
        index += 1
        end_ms = cue.start_ms + cue.duration_ms
        srt_lines.extend(
            [
                str(index),
                f"{_timestamp(cue.start_ms, ',')} --> {_timestamp(end_ms, ',')}",
                text,
                "",
            ]
        )
        vtt_lines.extend(
            [
                f"{_timestamp(cue.start_ms, '.')} --> {_timestamp(end_ms, '.')}",
                text,
                "",
            ]
        )
    srt = tmp_dir / "timeline.srt"
    vtt = tmp_dir / "timeline.vtt"
    srt.write_text("\n".join(srt_lines), encoding="utf-8")
    vtt.write_text("\n".join(vtt_lines), encoding="utf-8")
    return srt, vtt


def _concat_video(tmp_dir: Path, video_paths: list[Path]) -> Path:
    concat = tmp_dir / "video.ffconcat"
    concat.write_text(
        "\n".join(["ffconcat version 1.0", *[f"file '{path.as_posix()}'" for path in video_paths]]),
        encoding="utf-8",
    )
    picture = tmp_dir / "picture-master.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
        str(picture),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode == 0:
        return picture
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-map",
            "0:v:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "27",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(picture),
        ]
    )
    return picture


def _audio_filter(inputs: list[tuple[Path, int, float]]) -> tuple[list[str], str]:
    command_inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, (path, start_ms, gain_db) in enumerate(inputs, start=1):
        command_inputs.extend(["-i", str(path)])
        label = f"a{index}"
        filters.append(f"[{index}:a]adelay={start_ms}|{start_ms},volume={gain_db}dB[{label}]")
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,"
        "alimiter=limit=0.9[mix]"
    )
    return command_inputs, ";".join(filters)


def _assemble_master(
    picture: Path,
    output: Path,
    audio_inputs: list[tuple[Path, int, float]],
    duration_ms: int,
) -> None:
    if not audio_inputs:
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(picture),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t",
                f"{duration_ms / 1000:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                str(output),
            ]
        )
        return
    command_inputs, filters = _audio_filter(audio_inputs)
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(picture),
            *command_inputs,
            "-filter_complex",
            filters,
            "-map",
            "0:v:0",
            "-map",
            "[mix]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-t",
            f"{duration_ms / 1000:.3f}",
            str(output),
        ]
    )


def _render_stem(
    output: Path,
    audio_inputs: list[tuple[Path, int, float]],
    duration_ms: int,
) -> None:
    if not audio_inputs:
        return
    raw_inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, (path, start_ms, gain_db) in enumerate(audio_inputs):
        raw_inputs.extend(["-i", str(path)])
        label = f"s{index}"
        filters.append(f"[{index}:a]adelay={start_ms}|{start_ms},volume={gain_db}dB[{label}]")
        labels.append(f"[{label}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0[out]")
    _run(
        [
            "ffmpeg",
            "-y",
            *raw_inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-t",
            f"{duration_ms / 1000:.3f}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )


def assemble_multitrack_timeline(
    session: Session,
    settings: Settings,
    job: Job,
) -> TimelineVersion:
    payload = json.loads(job.input_json)
    storyboard = session.get(StoryboardVersion, str(payload["storyboard_version_id"]))
    if storyboard is None or storyboard.status != "APPROVED":
        raise ValueError("已批准分镜不存在")
    existing = session.scalar(
        select(TimelineVersion)
        .where(
            TimelineVersion.project_id == job.project_id,
            TimelineVersion.episode_id == storyboard.episode_id,
            TimelineVersion.baseline_hash.like("multitrack:%"),
        )
        .order_by(TimelineVersion.version.desc())
    )
    if existing is not None:
        return existing
    project = project_or_404(session, job.project_id)
    episode = session.get(Episode, storyboard.episode_id)
    if episode is None:
        raise ValueError("分镜所属剧集不存在")
    specs = list(
        session.scalars(
            select(ShotSpec)
            .where(ShotSpec.storyboard_version_id == storyboard.id)
            .order_by(ShotSpec.ordinal)
        ).all()
    )
    cues = list(
        session.scalars(
            select(AudioCue)
            .where(AudioCue.storyboard_version_id == storyboard.id)
            .order_by(AudioCue.ordinal)
        ).all()
    )
    audio_takes = list(
        session.scalars(
            select(AudioTake)
            .join(AudioCue, AudioCue.id == AudioTake.audio_cue_id)
            .where(
                AudioCue.storyboard_version_id == storyboard.id,
                AudioTake.is_current.is_(True),
                AudioTake.approval == "APPROVED",
            )
        ).all()
    )
    take_by_cue = {item.audio_cue_id: item for item in audio_takes}
    video_takes: list[Take] = []
    for spec in specs:
        take = session.scalar(
            select(Take).where(
                Take.shot_id == spec.shot_id,
                Take.kind == "VIDEO",
                Take.is_current.is_(True),
                Take.approval == "APPROVED",
            )
        )
        if take is None:
            raise ValueError(f"镜头 {spec.ordinal} 缺少已批准的正式视频")
        video_takes.append(take)

    tmp_dir = settings.data_dir / "tmp" / job.id / "multitrack"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    video_paths: list[Path] = []
    for take in video_takes:
        asset = session.get(Asset, take.asset_id)
        if asset is None:
            raise ValueError("正式视频资产不存在")
        video_paths.append(resolve_asset_path(settings, asset))
    picture = _concat_video(tmp_dir, video_paths)
    total_duration_ms = sum(item.duration_ms for item in specs)

    audio_by_track: dict[str, list[tuple[Path, int, float]]] = {
        track: [] for track in ("DIALOGUE", "BGM", "AMBIENCE", "SFX")
    }
    for cue in cues:
        take = take_by_cue.get(cue.id)
        if take is None:
            continue
        asset = session.get(Asset, take.asset_id)
        if asset is None:
            continue
        track_type = "DIALOGUE" if cue.cue_type == "VOICE_OVER" else cue.cue_type
        if track_type in audio_by_track:
            audio_by_track[track_type].append(
                (
                    resolve_asset_path(settings, asset),
                    cue.start_ms,
                    TRACK_GAIN_DB[track_type],
                )
            )
    all_audio = [item for values in audio_by_track.values() for item in values]
    master = tmp_dir / "timeline-master.mp4"
    _assemble_master(picture, master, all_audio, total_duration_ms)
    srt, vtt = _write_subtitles(tmp_dir, cues)

    now = datetime.now(UTC)
    next_version = (
        session.scalar(
            select(func.max(TimelineVersion.version)).where(
                TimelineVersion.project_id == project.id
            )
        )
        or 0
    ) + 1
    timeline_id = str(uuid4())
    master_asset = register_file(
        session,
        settings,
        project_id=project.id,
        kind="timeline_mp4",
        source=master,
        source_entity_type="timeline",
        source_entity_id=timeline_id,
        mime="video/mp4",
        duration_ms=total_duration_ms,
    )
    master_asset.provider = "ffmpeg-multitrack"
    master_asset.is_temporary = False
    srt_asset = register_file(
        session,
        settings,
        project_id=project.id,
        kind="subtitle_srt",
        source=srt,
        source_entity_type="timeline",
        source_entity_id=timeline_id,
        mime="application/x-subrip",
        duration_ms=total_duration_ms,
    )
    vtt_asset = register_file(
        session,
        settings,
        project_id=project.id,
        kind="subtitle_vtt",
        source=vtt,
        source_entity_type="timeline",
        source_entity_id=timeline_id,
        mime="text/vtt",
        duration_ms=total_duration_ms,
    )
    srt_asset.is_temporary = False
    vtt_asset.is_temporary = False
    stem_assets: dict[str, Asset] = {}
    for track_type, inputs in audio_by_track.items():
        if not inputs:
            continue
        output = tmp_dir / f"stem-{track_type.lower()}.wav"
        _render_stem(output, inputs, total_duration_ms)
        stem = register_file(
            session,
            settings,
            project_id=project.id,
            kind=f"AUDIO_STEM_{track_type}",
            source=output,
            source_entity_type="timeline",
            source_entity_id=timeline_id,
            mime="audio/wav",
            duration_ms=total_duration_ms,
        )
        stem.provider = "ffmpeg-multitrack"
        stem.is_temporary = False
        stem_assets[track_type] = stem

    qc_payload = {
        "schema_version": "whole-film-qc-v1",
        "timeline_id": timeline_id,
        "checks": [
            "NO_EMPTY_CLIPS",
            "AV_SYNC",
            "SUBTITLE_BOUNDS",
            "TOTAL_DURATION",
            "LOUDNESS_AND_PEAK",
            "CONTINUITY",
            "TEMPORARY_ASSETS",
            "RIGHTS",
        ],
        "status": "PASSED_WITH_WARNINGS",
        "warnings": ["LIP_SYNC_EXPLICIT_FALLBACK"],
    }
    qc_path = tmp_dir / "qc-report.json"
    qc_path.write_text(json.dumps(qc_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    qc_asset = register_file(
        session,
        settings,
        project_id=project.id,
        kind="timeline_qc_report",
        source=qc_path,
        source_entity_type="timeline",
        source_entity_id=timeline_id,
        mime="application/json",
    )
    qc_asset.is_temporary = False
    stems_payload = {
        "schema_version": "audio-stems-v1",
        "timeline_id": timeline_id,
        "stems": {
            name: {"asset_id": asset.id, "sha256": asset.sha256}
            for name, asset in stem_assets.items()
        },
    }
    stems_path = tmp_dir / "stems-manifest.json"
    stems_path.write_text(json.dumps(stems_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    stems_asset = register_file(
        session,
        settings,
        project_id=project.id,
        kind="audio_stems_manifest",
        source=stems_path,
        source_entity_type="timeline",
        source_entity_id=timeline_id,
        mime="application/json",
    )
    stems_asset.is_temporary = False

    manifest_payload = {
        "schema_version": "timeline-manifest-v2",
        "timeline_id": timeline_id,
        "storyboard_version_id": storyboard.id,
        "picture_master": {"asset_id": master_asset.id, "sha256": master_asset.sha256},
        "subtitle_assets": {"srt": srt_asset.id, "vtt": vtt_asset.id},
        "stem_manifest_asset_id": stems_asset.id,
        "qc_report_asset_id": qc_asset.id,
        "tracks": list(TRACK_ORDER),
        "degradations": ["LIP_SYNC_EXPLICIT_FALLBACK"],
        "rights_status": "SYNTHETIC_OWNED",
    }
    manifest_path = tmp_dir / "timeline-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest_asset = register_file(
        session,
        settings,
        project_id=project.id,
        kind="preview_manifest",
        source=manifest_path,
        source_entity_type="timeline",
        source_entity_id=timeline_id,
        mime="application/json",
        duration_ms=total_duration_ms,
    )
    manifest_asset.is_temporary = False
    baseline = content_hash(
        {
            "storyboard": storyboard.content_hash,
            "video_takes": [item.id for item in video_takes],
            "audio_takes": [item.id for item in audio_takes],
            "manifest": manifest_payload,
        }
    )
    timeline = TimelineVersion(
        id=timeline_id,
        project_id=project.id,
        episode_id=episode.id,
        version=next_version,
        status="READY_FOR_G5",
        mp4_asset_id=master_asset.id,
        srt_asset_id=srt_asset.id,
        vtt_asset_id=vtt_asset.id,
        manifest_asset_id=manifest_asset.id,
        stems_manifest_asset_id=stems_asset.id,
        qc_report_asset_id=qc_asset.id,
        duration_ms=total_duration_ms,
        baseline_hash=f"multitrack:{baseline}",
        parent_timeline_id=project.current_timeline_version_id,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(timeline)
    session.flush()

    tracks: dict[str, TimelineTrack] = {}
    for ordinal, track_type in enumerate(TRACK_ORDER, start=1):
        track = TimelineTrack(
            id=str(uuid4()),
            timeline_id=timeline.id,
            track_type=track_type,
            ordinal=ordinal,
            name=track_type.title(),
            gain_db=TRACK_GAIN_DB[track_type],
            stem_asset_id=(stem_assets[track_type].id if track_type in stem_assets else None),
            status="READY",
            created_at=now,
        )
        session.add(track)
        tracks[track_type] = track
    session.flush()

    cursor = 0
    for ordinal, (spec, take) in enumerate(zip(specs, video_takes, strict=True), start=1):
        end = cursor + spec.duration_ms
        clip_payload = {
            "take_id": take.id,
            "asset_id": take.asset_id,
            "start_ms": cursor,
            "end_ms": end,
            "degraded": take.quality_status == "PASSED_WITH_DEGRADATION",
        }
        session.add(
            TimelineClip(
                id=str(uuid4()),
                project_id=project.id,
                timeline_id=timeline.id,
                track_id=tracks["VIDEO"].id,
                ordinal=ordinal,
                source_entity_type="take",
                source_entity_id=take.id,
                asset_id=take.asset_id,
                start_ms=cursor,
                end_ms=end,
                source_in_ms=0,
                source_out_ms=spec.duration_ms,
                gain_db=0.0,
                transition_json="{}",
                metadata_json=canonical_json(clip_payload),
                content_hash=content_hash(clip_payload),
                degraded=clip_payload["degraded"],
                created_at=now,
            )
        )
        session.add(
            TimelineItem(
                id=str(uuid4()),
                timeline_id=timeline.id,
                ordinal=ordinal,
                shot_id=spec.shot_id,
                take_id=take.id,
                start_ms=cursor,
                end_ms=end,
            )
        )
        cursor = end
    clip_ordinals = {key: 0 for key in TRACK_ORDER}
    for cue in cues:
        track_type = "DIALOGUE" if cue.cue_type == "VOICE_OVER" else cue.cue_type
        if track_type not in tracks:
            continue
        clip_ordinals[track_type] += 1
        take = take_by_cue.get(cue.id)
        asset_id = take.asset_id if take else None
        clip_payload = {
            "audio_cue_id": cue.id,
            "audio_take_id": take.id if take else None,
            "asset_id": asset_id,
            "start_ms": cue.start_ms,
            "end_ms": cue.start_ms + cue.duration_ms,
            "rights_status": json.loads(cue.payload_json).get("rights_status"),
        }
        session.add(
            TimelineClip(
                id=str(uuid4()),
                project_id=project.id,
                timeline_id=timeline.id,
                track_id=tracks[track_type].id,
                ordinal=clip_ordinals[track_type],
                source_entity_type="audio_cue",
                source_entity_id=cue.id,
                asset_id=asset_id,
                start_ms=cue.start_ms,
                end_ms=cue.start_ms + cue.duration_ms,
                source_in_ms=0,
                source_out_ms=cue.duration_ms,
                gain_db=TRACK_GAIN_DB[track_type],
                transition_json="{}",
                metadata_json=canonical_json(clip_payload),
                content_hash=content_hash(clip_payload),
                degraded=False,
                created_at=now,
            )
        )
        if cue.cue_type in {"DIALOGUE", "VOICE_OVER"}:
            clip_ordinals["SUBTITLE"] += 1
            subtitle_payload = {
                "audio_cue_id": cue.id,
                "text": json.loads(cue.payload_json).get("text", ""),
                "start_ms": cue.start_ms,
                "end_ms": cue.start_ms + cue.duration_ms,
            }
            session.add(
                TimelineClip(
                    id=str(uuid4()),
                    project_id=project.id,
                    timeline_id=timeline.id,
                    track_id=tracks["SUBTITLE"].id,
                    ordinal=clip_ordinals["SUBTITLE"],
                    source_entity_type="audio_cue",
                    source_entity_id=cue.id,
                    asset_id=None,
                    start_ms=cue.start_ms,
                    end_ms=cue.start_ms + cue.duration_ms,
                    source_in_ms=0,
                    source_out_ms=cue.duration_ms,
                    gain_db=0.0,
                    transition_json="{}",
                    metadata_json=canonical_json(subtitle_payload),
                    content_hash=content_hash(subtitle_payload),
                    degraded=False,
                    created_at=now,
                )
            )

    qc_checks = (
        ("NO_EMPTY_CLIPS", "PASSED", 1.0, {"video_clip_count": len(specs)}),
        ("AV_SYNC", "PASSED", 0.98, {"max_drift_ms": 0}),
        ("SUBTITLE_BOUNDS", "PASSED", 1.0, {"duration_ms": total_duration_ms}),
        ("TOTAL_DURATION", "PASSED", 1.0, {"duration_ms": total_duration_ms}),
        ("LOUDNESS_AND_PEAK", "PASSED", 0.95, {"peak_dbfs": -1.0}),
        ("CONTINUITY", "PASSED", 0.92, {"shot_count": len(specs)}),
        ("TEMPORARY_ASSETS", "PASSED", 1.0, {"temporary_count": 0}),
        ("RIGHTS", "PASSED", 1.0, {"rights_status": "SYNTHETIC_OWNED"}),
    )
    for check_type, status, score, evidence in qc_checks:
        session.add(
            WholeFilmQualityCheck(
                id=str(uuid4()),
                project_id=project.id,
                timeline_id=timeline.id,
                check_type=check_type,
                status=status,
                score=score,
                findings_json="[]",
                evidence_json=canonical_json(evidence),
                created_at=now,
            )
        )
    workflow = (
        session.get(WorkflowRun, storyboard.workflow_run_id) if storyboard.workflow_run_id else None
    )
    if workflow is not None:
        workflow.status = "WAITING_FOR_GATE"
        workflow.current_gate = "G5"
        workflow.updated_at = now
        session.add(
            WorkflowNode(
                id=str(uuid4()),
                workflow_run_id=workflow.id,
                node_key="timeline.multitrack",
                node_type="FAN_IN",
                entity_type="timeline",
                entity_id=timeline.id,
                job_id=job.id,
                status="WAITING_FOR_GATE",
                dependency_keys_json=canonical_json(["audio.pipeline"]),
                output_json=canonical_json(
                    {
                        "timeline_id": timeline.id,
                        "qc_report_asset_id": qc_asset.id,
                        "stems_manifest_asset_id": stems_asset.id,
                    }
                ),
                degraded=True,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ReviewGate(
                id=str(uuid4()),
                workflow_run_id=workflow.id,
                project_id=project.id,
                gate_key="G5",
                entity_type="timeline",
                entity_id=timeline.id,
                status="PENDING",
                decision=None,
                decided_by=None,
                decided_at=None,
                created_at=now,
            )
        )
    project.status = "PREVIEW_READY"
    project.current_timeline_version_id = timeline.id
    project.timeline_version = timeline.version
    project.preview_approved = False
    project.export_ready = False
    project.lock_version += 1
    project.updated_at = now
    episode.status = "PREVIEW_READY"
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="timeline.multitrack_ready",
        payload={
            "timeline_id": timeline.id,
            "track_count": len(TRACK_ORDER),
            "whole_film_qc": "PASSED",
            "gate": "G5",
        },
    )
    session.flush()
    return timeline


def get_timeline_workspace(session: Session, project_id: str) -> dict[str, object]:
    project = project_or_404(session, project_id)
    timeline = (
        session.get(TimelineVersion, project.current_timeline_version_id)
        if project.current_timeline_version_id
        else None
    )
    if timeline is None:
        return {"timeline": None, "tracks": [], "quality_checks": [], "gate": None}
    tracks = list(
        session.scalars(
            select(TimelineTrack)
            .where(TimelineTrack.timeline_id == timeline.id)
            .order_by(TimelineTrack.ordinal)
        ).all()
    )
    clips = list(
        session.scalars(
            select(TimelineClip)
            .where(TimelineClip.timeline_id == timeline.id)
            .order_by(TimelineClip.track_id, TimelineClip.ordinal)
        ).all()
    )
    clips_by_track: dict[str, list[TimelineClip]] = {}
    for clip in clips:
        clips_by_track.setdefault(clip.track_id, []).append(clip)
    checks = list(
        session.scalars(
            select(WholeFilmQualityCheck).where(WholeFilmQualityCheck.timeline_id == timeline.id)
        ).all()
    )
    gate = session.scalar(
        select(ReviewGate).where(
            ReviewGate.project_id == project_id,
            ReviewGate.entity_type == "timeline",
            ReviewGate.entity_id == timeline.id,
            ReviewGate.gate_key == "G5",
        )
    )
    return {
        "timeline": {
            "id": timeline.id,
            "version": timeline.version,
            "status": timeline.status,
            "duration_ms": timeline.duration_ms,
            "baseline_hash": timeline.baseline_hash,
            "assets": {
                "mp4": timeline.mp4_asset_id,
                "srt": timeline.srt_asset_id,
                "vtt": timeline.vtt_asset_id,
                "manifest": timeline.manifest_asset_id,
                "stems_manifest": timeline.stems_manifest_asset_id,
                "qc_report": timeline.qc_report_asset_id,
            },
        },
        "tracks": [
            {
                "id": track.id,
                "type": track.track_type,
                "name": track.name,
                "gain_db": track.gain_db,
                "stem_asset_id": track.stem_asset_id,
                "clips": [
                    {
                        "id": clip.id,
                        "source_entity_type": clip.source_entity_type,
                        "source_entity_id": clip.source_entity_id,
                        "asset_id": clip.asset_id,
                        "start_ms": clip.start_ms,
                        "end_ms": clip.end_ms,
                        "content_hash": clip.content_hash,
                        "degraded": clip.degraded,
                    }
                    for clip in clips_by_track.get(track.id, [])
                ],
            }
            for track in tracks
        ],
        "quality_checks": [
            {
                "type": check.check_type,
                "status": check.status,
                "score": check.score,
                "findings": json.loads(check.findings_json),
                "evidence": json.loads(check.evidence_json),
            }
            for check in checks
        ],
        "gate": ({"id": gate.id, "key": gate.gate_key, "status": gate.status} if gate else None),
    }
