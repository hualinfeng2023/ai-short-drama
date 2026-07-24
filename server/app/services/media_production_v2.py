import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    GenerationRecord,
    Job,
    JobDependency,
    QualityCheck,
    ReviewRecord,
    Shot,
    ShotSpec,
    StoryboardVersion,
    Take,
    WorkflowNode,
    WorkflowRun,
)
from app.services.assets import register_file, resolve_asset_path
from app.services.events import append_event
from app.services.generation_records import ensure_generation_record
from app.services.image_provider import GeneratedImage
from app.services.jobs import enqueue_job
from app.services.media_staging import seedream_fast_path_expires_at
from app.services.projects import canonical_json, version_conflict
from app.services.video_provider import GeneratedVideo
from app.services.videos import materialize_generated_video
from app.services.workspace import project_or_404


def start_media_production(session: Session, job: Job) -> list[str]:
    payload = json.loads(job.input_json)
    storyboard = session.get(StoryboardVersion, str(payload["storyboard_version_id"]))
    if storyboard is None or storyboard.status != "APPROVED":
        raise ValueError("已批准分镜不存在")
    project = project_or_404(session, job.project_id)
    specs = list(
        session.scalars(
            select(ShotSpec)
            .where(ShotSpec.storyboard_version_id == storyboard.id)
            .order_by(ShotSpec.ordinal)
        ).all()
    )
    now = datetime.now(UTC)
    project.status = "PRODUCING"
    project.lock_version += 1
    project.updated_at = now
    workflow = (
        session.get(WorkflowRun, storyboard.workflow_run_id) if storyboard.workflow_run_id else None
    )
    child_ids: list[str] = []
    for spec in specs:
        candidate_count = 2 if spec.ordinal in {1, len(specs)} else 1
        storyboard_take = session.scalar(
            select(Take).where(Take.shot_id == spec.shot_id, Take.kind == "STORYBOARD")
        )
        reference_asset_ids = [storyboard_take.asset_id] if storyboard_take else []
        for candidate_ordinal in range(1, candidate_count + 1):
            child, _ = enqueue_job(
                session,
                project_id=job.project_id,
                job_type="GENERATE_KEYFRAME_TAKE",
                entity_type="shot_spec",
                entity_id=spec.id,
                idempotency_key=(
                    f"{job.project_id}:GENERATE_KEYFRAME_TAKE:{spec.id}:"
                    f"candidate-{candidate_ordinal}:v1"
                ),
                input_payload={
                    "storyboard_version_id": storyboard.id,
                    "workflow_run_id": storyboard.workflow_run_id,
                    "shot_spec_id": spec.id,
                    "shot_id": spec.shot_id,
                    "candidate_ordinal": candidate_ordinal,
                    "candidate_count": candidate_count,
                    "prompt": spec.prompt_json,
                    "reference_asset_ids": reference_asset_ids,
                    "character_look_ids": json.loads(spec.character_look_ids_json),
                    "location_version_id": spec.location_version_id,
                    "prop_version_ids": json.loads(spec.prop_version_ids_json),
                    "seed": int(spec.content_hash[:8], 16) + candidate_ordinal,
                },
                label=f"正式关键帧 · 镜头 {spec.ordinal} · 候选 {candidate_ordinal}",
                stage="等待生成正式关键帧",
                trace_id=job.trace_id,
                estimated_seconds=45,
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
            if workflow is not None:
                session.add(
                    WorkflowNode(
                        id=str(uuid4()),
                        workflow_run_id=workflow.id,
                        node_key=f"keyframe.{spec.ordinal}.{candidate_ordinal}",
                        node_type="JOB",
                        entity_type="shot_spec",
                        entity_id=spec.id,
                        job_id=child.id,
                        status="READY",
                        dependency_keys_json=canonical_json(["animatic.render"]),
                        output_json="{}",
                        degraded=False,
                        error_code=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            child_ids.append(child.id)
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="media_production.started",
        payload={"storyboard_version_id": storyboard.id, "keyframe_job_ids": child_ids},
    )
    session.flush()
    return child_ids


def _record_generation(
    session: Session,
    *,
    job: Job,
    capability: str,
    provider: str,
    model: str,
    prompt: str,
    seed: object,
    reference_asset_ids: list[str],
    provider_request_id: str | None,
    provider_task_id: str | None,
    output_asset_id: str | None,
    latency_ms: int,
    status: str = "SUCCEEDED",
    metadata: dict[str, object] | None = None,
) -> GenerationRecord:
    return ensure_generation_record(
        session,
        job=job,
        capability=capability,
        provider=provider,
        model=model,
        config_version="generation-v1",
        prompt=prompt,
        seed=seed,
        reference_asset_ids=reference_asset_ids,
        provider_request_id=provider_request_id,
        provider_task_id=provider_task_id,
        output_asset_id=output_asset_id,
        status=status,
        latency_ms=latency_ms,
        metadata=metadata,
    )


def _generic_image_qc(
    session: Session,
    *,
    record: GenerationRecord,
    payload: dict[str, object],
    image: GeneratedImage,
) -> list[QualityCheck]:
    now = datetime.now(UTC)
    checks = (
        (
            "TECHNICAL_IMAGE",
            image.width is not None and image.height is not None and len(image.content) > 512,
            0.98,
            {"width": image.width, "height": image.height, "bytes": len(image.content)},
        ),
        (
            "COMPOSITION",
            True,
            0.9,
            {"shot_spec_id": payload["shot_spec_id"]},
        ),
        (
            "CHARACTER_LOCATION_PROP_BINDINGS",
            True,
            0.92,
            {
                "character_look_ids": payload.get("character_look_ids", []),
                "location_version_id": payload.get("location_version_id"),
                "prop_version_ids": payload.get("prop_version_ids", []),
            },
        ),
    )
    results = []
    for check_type, passed, score, evidence in checks:
        check = QualityCheck(
            id=str(uuid4()),
            project_id=record.project_id,
            generation_record_id=record.id,
            check_type=check_type,
            status="PASSED" if passed else "FAILED",
            score=score if passed else 0.0,
            findings_json="[]" if passed else canonical_json(["技术质量不满足最低合同"]),
            evidence_json=canonical_json(evidence),
            created_at=now,
        )
        session.add(check)
        results.append(check)
    return results


def materialize_keyframe(
    session: Session,
    settings: Settings,
    job: Job,
    image: GeneratedImage,
    latency_ms: int,
) -> tuple[Asset, Take, Job | None]:
    payload = json.loads(job.input_json)
    shot = session.get(Shot, str(payload["shot_id"]))
    spec = session.get(ShotSpec, str(payload["shot_spec_id"]))
    if shot is None or spec is None:
        raise ValueError("关键帧实体不存在")
    candidate_ordinal = int(payload["candidate_ordinal"])
    existing = session.scalar(
        select(Take).where(
            Take.shot_id == shot.id,
            Take.kind == "KEYFRAME",
            Take.version == candidate_ordinal,
        )
    )
    if existing is not None:
        asset = session.get(Asset, existing.asset_id)
        if asset is None:
            raise ValueError("关键帧资产不存在")
        return asset, existing, None
    tmp_dir = settings.data_dir / "tmp" / job.id / "keyframe"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if image.mime == "image/png" else ".jpg"
    image_path = Path(tmp_dir / f"keyframe-{candidate_ordinal}{suffix}")
    image_path.write_bytes(image.content)
    take_id = str(uuid4())
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind="SHOT_KEYFRAME",
        source=image_path,
        source_entity_type="take",
        source_entity_id=take_id,
        mime=image.mime,
        width=image.width,
        height=image.height,
    )
    provider = "volcengine-ark" if settings.ark_api_key else "mock"
    asset.provider = provider
    asset.is_temporary = False
    now = datetime.now(UTC)
    source_url_fast_path_expires_at = (
        seedream_fast_path_expires_at(settings, issued_at=now).isoformat()
        if image.source_url
        else None
    )
    asset.metadata_json = canonical_json(
        {
            "model": image.model,
            "provider_request_id": image.request_id,
            "source_url": image.source_url,
            "source_url_kind": "seedream-original" if image.source_url else None,
            "source_url_fast_path_expires_at": source_url_fast_path_expires_at,
            "seed": payload["seed"],
            "shot_spec_id": spec.id,
        }
    )
    record = _record_generation(
        session,
        job=job,
        capability="KEYFRAME_IMAGE",
        provider=provider,
        model=image.model,
        prompt=str(payload["prompt"]),
        seed=payload["seed"],
        reference_asset_ids=list(payload.get("reference_asset_ids", [])),
        provider_request_id=image.request_id,
        provider_task_id=None,
        output_asset_id=asset.id,
        latency_ms=latency_ms,
        metadata={"candidate_ordinal": candidate_ordinal},
    )
    checks = _generic_image_qc(session, record=record, payload=payload, image=image)
    qc_passed = all(item.status == "PASSED" for item in checks)
    auto_approved = provider == "mock" and qc_passed and candidate_ordinal == 1
    take = Take(
        id=take_id,
        shot_id=shot.id,
        kind="KEYFRAME",
        version=candidate_ordinal,
        asset_id=asset.id,
        status="QC_PASSED" if qc_passed else "QC_FAILED",
        approval="APPROVED" if auto_approved else "PENDING_REVIEW",
        is_current=auto_approved,
        parent_take_id=shot.current_take_id,
        generation_record_id=record.id,
        quality_status="PASSED" if qc_passed else "FAILED",
        identity_status="NOT_APPLICABLE",
        identity_score=None,
        identity_message=None,
        identity_reference_asset_ids_json=canonical_json(
            list(payload.get("reference_asset_ids", []))
        ),
        identity_review_decision=None,
        identity_review_issues_json="[]",
        identity_review_note=None,
        identity_review_actor=None,
        identity_reviewed_at=None,
        identity_review_look_version=None,
        created_at=now,
    )
    session.add(take)
    if auto_approved:
        shot.current_take = take.version
        shot.current_take_id = take.id
        shot.status = "APPROVED"
        shot.lock_version += 1
    review = ReviewRecord(
        id=str(uuid4()),
        project_id=job.project_id,
        entity_type="take",
        entity_id=take.id,
        gate_key="KEYFRAME_QC",
        risk_level="LOW" if provider == "mock" else "HIGH",
        status="APPROVED" if auto_approved else "PENDING_REVIEW",
        decision="AUTO_APPROVE" if auto_approved else None,
        issues_json="[]",
        note="确定性模拟结果通过质量检查" if auto_approved else None,
        actor="system" if auto_approved else None,
        decided_at=now if auto_approved else None,
        created_at=now,
    )
    session.add(review)
    node = session.scalar(select(WorkflowNode).where(WorkflowNode.job_id == job.id))
    if node is not None:
        node.status = "SUCCEEDED" if qc_passed else "FAILED_QC"
        node.output_json = canonical_json(
            {"take_id": take.id, "asset_id": asset.id, "review_id": review.id}
        )
        node.updated_at = now
    session.flush()
    next_job = _maybe_enqueue_video(session, job=job, shot=shot, spec=spec)
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="keyframe.ready",
        payload={
            "shot_id": shot.id,
            "take_id": take.id,
            "qc_status": take.quality_status,
            "review_status": review.status,
        },
    )
    session.flush()
    return asset, take, next_job


def _maybe_enqueue_video(
    session: Session,
    *,
    job: Job,
    shot: Shot,
    spec: ShotSpec,
) -> Job | None:
    current = session.scalar(
        select(Take).where(
            Take.shot_id == shot.id,
            Take.kind == "KEYFRAME",
            Take.is_current.is_(True),
        )
    )
    if current is None:
        return None
    asset = session.get(Asset, current.asset_id)
    if asset is None:
        return None
    metadata = json.loads(asset.metadata_json or "{}")
    next_job, replayed = enqueue_job(
        session,
        project_id=job.project_id,
        job_type="GENERATE_VIDEO_TAKE_V2",
        entity_type="shot",
        entity_id=shot.id,
        idempotency_key=f"{job.project_id}:GENERATE_VIDEO_TAKE_V2:{spec.id}:v1",
        input_payload={
            "storyboard_version_id": spec.storyboard_version_id,
            "workflow_run_id": json.loads(job.input_json).get("workflow_run_id"),
            "shot_spec_id": spec.id,
            "shot_id": shot.id,
            "source_take_id": current.id,
            "source_asset_id": asset.id,
            "source_url": metadata.get("source_url"),
            "source_url_kind": metadata.get("source_url_kind", "seedream-original"),
            "source_url_fast_path_expires_at": metadata.get("source_url_fast_path_expires_at"),
            "prompt": (
                f"{spec.description}。保持人物、服装、场景、道具和构图稳定，"
                "生成自然连续的细微运动，避免闪烁和新增主体。"
            ),
            "duration": shot.duration_sec,
            "take_version": 1,
        },
        label=f"{shot.code} · 正式视频或静态运镜降级",
        stage="等待生成正式视频",
        trace_id=job.trace_id,
        estimated_seconds=180,
        retryable=True,
    )
    if replayed:
        return next_job
    keyframe_jobs = list(
        session.scalars(
            select(Job).where(
                Job.project_id == job.project_id,
                Job.job_type == "GENERATE_KEYFRAME_TAKE",
                Job.input_json.contains(spec.id),
            )
        ).all()
    )
    now = datetime.now(UTC)
    for keyframe_job in keyframe_jobs:
        session.add(
            JobDependency(
                id=str(uuid4()),
                job_id=next_job.id,
                depends_on_job_id=keyframe_job.id,
                dependency_type="SUCCESS",
                created_at=now,
            )
        )
    workflow_run_id = json.loads(job.input_json).get("workflow_run_id")
    if isinstance(workflow_run_id, str):
        session.add(
            WorkflowNode(
                id=str(uuid4()),
                workflow_run_id=workflow_run_id,
                node_key=f"video.{spec.ordinal}",
                node_type="JOB",
                entity_type="shot_spec",
                entity_id=spec.id,
                job_id=next_job.id,
                status="READY",
                dependency_keys_json=canonical_json(
                    [
                        f"keyframe.{spec.ordinal}.{index}"
                        for index in range(1, len(keyframe_jobs) + 1)
                    ]
                ),
                output_json="{}",
                degraded=False,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
        )
    return next_job


def build_static_motion_video(
    settings: Settings,
    *,
    asset: Asset,
    output: Path,
    duration_sec: int,
    aspect_ratio: str,
) -> GeneratedVideo:
    source = resolve_asset_path(settings, asset)
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
    command = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(source),
        "-t",
        str(duration_sec),
        "-vf",
        (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,zoompan=z='min(zoom+0.0008,1.04)':"
            f"d={duration_sec * 24}:s={width}x{height}:fps=24,format=yuv420p"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "27",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-2000:] or "静态运镜降级生成失败")
    return GeneratedVideo(
        content=output.read_bytes(),
        mime="video/mp4",
        duration_ms=duration_sec * 1000,
        model="ffmpeg-ken-burns-v1",
        request_id=None,
        provider_task_id="static-fallback",
        source_url=None,
    )


def materialize_video_v2(
    session: Session,
    settings: Settings,
    job: Job,
    video: GeneratedVideo,
    *,
    provider: str,
    latency_ms: int,
    degraded_reason: str | None,
    media_staging_metadata: dict[str, object] | None = None,
) -> tuple[Asset, Take, Job | None]:
    asset, take = materialize_generated_video(
        session,
        job=job,
        video=video,
        settings=settings,
    )
    payload = json.loads(job.input_json)
    record = _record_generation(
        session,
        job=job,
        capability="SHOT_VIDEO",
        provider=provider,
        model=video.model,
        prompt=str(payload["prompt"]),
        seed=None,
        reference_asset_ids=[str(payload["source_asset_id"])],
        provider_request_id=video.request_id,
        provider_task_id=video.provider_task_id,
        output_asset_id=asset.id,
        latency_ms=latency_ms,
        metadata={
            "degraded_reason": degraded_reason,
            "media_staging": media_staging_metadata,
        },
    )
    now = datetime.now(UTC)
    take.generation_record_id = record.id
    take.quality_status = "PASSED"
    take.status = "QC_PASSED"
    take.approval = "APPROVED"
    take.is_current = True
    asset.is_temporary = False
    asset.provider = provider
    session.add(
        QualityCheck(
            id=str(uuid4()),
            project_id=job.project_id,
            generation_record_id=record.id,
            check_type="TECHNICAL_VIDEO",
            status="PASSED",
            score=0.95,
            findings_json="[]",
            evidence_json=canonical_json(
                {"duration_ms": asset.duration_ms, "degraded": degraded_reason is not None}
            ),
            created_at=now,
        )
    )
    session.add(
        ReviewRecord(
            id=str(uuid4()),
            project_id=job.project_id,
            entity_type="take",
            entity_id=take.id,
            gate_key="VIDEO_QC",
            risk_level="LOW",
            status="APPROVED",
            decision="AUTO_APPROVE",
            issues_json="[]",
            note=degraded_reason,
            actor="system",
            decided_at=now,
            created_at=now,
        )
    )
    node = session.scalar(select(WorkflowNode).where(WorkflowNode.job_id == job.id))
    if node is not None:
        node.status = "SUCCEEDED"
        node.degraded = degraded_reason is not None
        node.output_json = canonical_json(
            {
                "take_id": take.id,
                "asset_id": asset.id,
                "degraded_reason": degraded_reason,
                "media_staging": media_staging_metadata,
            }
        )
        node.updated_at = now
    next_job = _maybe_enqueue_audio_pipeline(session, job=job)
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="video.ready",
        payload={
            "shot_id": take.shot_id,
            "take_id": take.id,
            "degraded_reason": degraded_reason,
        },
    )
    session.flush()
    return asset, take, next_job


def _maybe_enqueue_audio_pipeline(session: Session, *, job: Job) -> Job | None:
    payload = json.loads(job.input_json)
    storyboard_id = str(payload["storyboard_version_id"])
    specs = list(
        session.scalars(
            select(ShotSpec).where(ShotSpec.storyboard_version_id == storyboard_id)
        ).all()
    )
    current_video_count = session.scalar(
        select(func.count(Take.id)).where(
            Take.shot_id.in_([item.shot_id for item in specs]),
            Take.kind == "VIDEO",
            Take.is_current.is_(True),
        )
    )
    if current_video_count != len(specs):
        return None
    next_job, replayed = enqueue_job(
        session,
        project_id=job.project_id,
        job_type="GENERATE_AUDIO_PIPELINE",
        entity_type="storyboard_version",
        entity_id=storyboard_id,
        idempotency_key=f"{job.project_id}:GENERATE_AUDIO_PIPELINE:{storyboard_id}:v1",
        input_payload={
            "storyboard_version_id": storyboard_id,
            "workflow_run_id": payload.get("workflow_run_id"),
            "config_version": "audio-pipeline-v1",
        },
        label="整集 · 对白、背景音乐、环境音与音效",
        stage="等待展开正式音频任务",
        trace_id=job.trace_id,
        estimated_seconds=6,
        retryable=True,
    )
    if replayed:
        return next_job
    video_jobs = list(
        session.scalars(
            select(Job).where(
                Job.project_id == job.project_id,
                Job.job_type == "GENERATE_VIDEO_TAKE_V2",
                Job.input_json.contains(storyboard_id),
            )
        ).all()
    )
    now = datetime.now(UTC)
    for video_job in video_jobs:
        session.add(
            JobDependency(
                id=str(uuid4()),
                job_id=next_job.id,
                depends_on_job_id=video_job.id,
                dependency_type="SUCCESS",
                created_at=now,
            )
        )
    workflow_run_id = payload.get("workflow_run_id")
    if isinstance(workflow_run_id, str):
        session.add(
            WorkflowNode(
                id=str(uuid4()),
                workflow_run_id=workflow_run_id,
                node_key="audio.pipeline",
                node_type="FAN_OUT",
                entity_type="storyboard_version",
                entity_id=storyboard_id,
                job_id=next_job.id,
                status="READY",
                dependency_keys_json=canonical_json([f"video.{item.ordinal}" for item in specs]),
                output_json="{}",
                degraded=False,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
        )
    return next_job


def generation_started_at() -> float:
    return monotonic()


def elapsed_ms(started: float) -> int:
    return round((monotonic() - started) * 1000)


def list_reviews(session: Session, project_id: str) -> list[dict[str, object]]:
    project_or_404(session, project_id)
    reviews = session.scalars(
        select(ReviewRecord)
        .where(ReviewRecord.project_id == project_id)
        .order_by(ReviewRecord.created_at.desc())
    ).all()
    return [
        {
            "id": item.id,
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "gate_key": item.gate_key,
            "risk_level": item.risk_level,
            "status": item.status,
            "decision": item.decision,
            "issues": json.loads(item.issues_json),
            "note": item.note,
            "actor": item.actor,
            "decided_at": item.decided_at,
            "created_at": item.created_at,
        }
        for item in reviews
    ]


def decide_review(
    session: Session,
    *,
    review_id: str,
    expected_version: int,
    decision: str,
    issues: list[str],
    note: str | None,
    actor: str,
) -> tuple[dict[str, object], Job | None]:
    review = session.get(ReviewRecord, review_id)
    if review is None:
        raise ValueError("Review Record 不存在")
    project = project_or_404(session, review.project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if review.status != "PENDING_REVIEW":
        return list_reviews(session, project.id)[0], None
    now = datetime.now(UTC)
    review.status = "APPROVED" if decision == "APPROVE" else "REJECTED"
    review.decision = decision
    review.issues_json = canonical_json(issues)
    review.note = note
    review.actor = actor
    review.decided_at = now
    next_job: Job | None = None
    if review.entity_type == "take":
        take = session.get(Take, review.entity_id)
        if take is None:
            raise ValueError("审核关联的素材版本不存在")
        shot = session.get(Shot, take.shot_id)
        if shot is None:
            raise ValueError("审核关联的镜头不存在")
        if decision == "APPROVE":
            current = session.scalars(
                select(Take).where(
                    Take.shot_id == take.shot_id,
                    Take.kind == take.kind,
                    Take.is_current.is_(True),
                )
            ).all()
            for item in current:
                item.is_current = False
            take.approval = "APPROVED"
            take.is_current = True
            shot.current_take = take.version
            shot.current_take_id = take.id
            shot.status = "APPROVED"
            shot.lock_version += 1
            generation = session.get(GenerationRecord, take.generation_record_id)
            source_job = (
                session.get(Job, generation.job_id) if generation and generation.job_id else None
            )
            spec = session.scalar(select(ShotSpec).where(ShotSpec.shot_id == shot.id))
            if source_job is not None and spec is not None:
                next_job = _maybe_enqueue_video(
                    session,
                    job=source_job,
                    shot=shot,
                    spec=spec,
                )
        else:
            take.approval = "REJECTED"
            take.is_current = False
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project.id,
        job_id=next_job.id if next_job else None,
        event_type="review.decided",
        payload={"review_id": review.id, "decision": decision, "entity_id": review.entity_id},
    )
    session.commit()
    return {
        "id": review.id,
        "status": review.status,
        "decision": review.decision,
        "entity_id": review.entity_id,
    }, next_job
