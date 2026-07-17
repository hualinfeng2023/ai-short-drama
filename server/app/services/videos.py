import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import Asset, Job, Shot, Take
from app.schemas import JobRead, ShotVideoGenerateRequest
from app.services.events import append_event
from app.services.jobs import ACTIVE_STATUSES, QUEUED_STATUSES, enqueue_job, job_to_read
from app.services.media_staging import (
    media_staging_configured,
    seedream_fast_path_expires_at,
    seedream_fast_path_usable,
)
from app.services.takes import _shot_project
from app.services.video_provider import GeneratedVideo
from app.services.workspace import shot_or_404

VIDEO_JOB_TYPE = "GENERATE_SHOT_VIDEO"


@dataclass(frozen=True)
class SeedreamFastPath:
    url: str
    expires_at: str


def _valid_public_image_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname)


def _seedream_fast_path(
    session: Session,
    settings: Settings,
    shot_id: str,
    take_version: int,
) -> SeedreamFastPath | None:
    jobs = session.scalars(
        select(Job)
        .where(
            Job.job_type == "GENERATE_SHOT_IMAGE",
            Job.entity_id == shot_id,
            Job.status == "SUCCEEDED",
        )
        .order_by(Job.completed_at.desc())
    ).all()
    for job in jobs:
        if not job.output_json:
            continue
        output = json.loads(job.output_json)
        if int(output.get("take_version", -1)) != take_version:
            continue
        source_url = output.get("source_url")
        explicit_expiry = output.get("source_url_fast_path_expires_at")
        issued_at = job.completed_at or job.created_at
        expires_at = (
            explicit_expiry
            if isinstance(explicit_expiry, str)
            else seedream_fast_path_expires_at(settings, issued_at=issued_at).isoformat()
        )
        if seedream_fast_path_usable(source_url, expires_at):
            return SeedreamFastPath(url=source_url, expires_at=expires_at)
    return None


def build_video_prompt(
    shot: Shot,
    payload: ShotVideoGenerateRequest,
) -> str:
    instruction = payload.prompt or (
        f"{shot.description}。人物和环境产生自然连续的细微运动，保持主体身份、服装、场景和构图稳定，"
        "电影感运镜，动作连贯，避免新增人物、文字和画面闪烁。"
    )
    camera_fixed = str(payload.camera_fixed).lower()
    watermark = str(payload.watermark).lower()
    return (
        f"{instruction} --duration {payload.duration} "
        f"--camerafixed {camera_fixed} --watermark {watermark}"
    )


def create_shot_video_job(
    session: Session,
    *,
    shot_id: str,
    payload: ShotVideoGenerateRequest,
    request_idempotency_key: str,
    trace_id: str,
) -> tuple[JobRead, bool]:
    shot = shot_or_404(session, shot_id)
    project = _shot_project(session, shot)
    take_version = shot.candidate_take or shot.current_take
    active = session.scalar(
        select(Job)
        .where(
            Job.job_type == VIDEO_JOB_TYPE,
            Job.entity_id == shot_id,
            Job.status.in_(QUEUED_STATUSES | ACTIVE_STATUSES),
        )
        .order_by(Job.created_at.desc())
    )
    if active is not None:
        return job_to_read(active), True

    existing_video = session.scalar(
        select(Take).where(
            Take.shot_id == shot.id,
            Take.kind == "VIDEO",
            Take.version == take_version,
        )
    )
    if existing_video is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SHOT_VIDEO_ALREADY_EXISTS",
                "message": f"素材第 {take_version} 版已有视频",
                "retryable": False,
            },
        )

    settings = get_settings()
    supplied_url = str(payload.image_url) if payload.image_url is not None else None
    seedream_source = (
        None
        if supplied_url is not None
        else _seedream_fast_path(session, settings, shot.id, take_version)
    )
    image_url = supplied_url or (seedream_source.url if seedream_source else None)
    source_url_kind = "user-supplied" if supplied_url else "seedream-original"
    source_url_fast_path_expires_at = seedream_source.expires_at if seedream_source else None
    source_take = session.scalar(
        select(Take)
        .where(
            Take.shot_id == shot.id,
            Take.kind.in_(["STILL", "KEYFRAME"]),
            Take.version == take_version,
        )
        .order_by(Take.created_at.desc())
    )
    source_asset_id = source_take.asset_id if source_take is not None else None
    can_stage = source_asset_id is not None and media_staging_configured(settings)
    if (image_url is None or not _valid_public_image_url(image_url)) and not can_stage:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PUBLIC_IMAGE_URL_REQUIRED",
                "message": "图生视频需要已批准的本地关键帧和 TOS 暂存，或手动填写 HTTPS 图片地址",
                "user_action": "配置私有 TOS 暂存，或填写公网可访问的图片地址",
                "retryable": False,
            },
        )

    prompt = build_video_prompt(shot, payload)
    job, replayed = enqueue_job(
        session,
        project_id=project.id,
        job_type=VIDEO_JOB_TYPE,
        entity_type="shot",
        entity_id=shot.id,
        idempotency_key=f"shot-video:{shot.id}:{request_idempotency_key}",
        input_payload={
            "shot_id": shot.id,
            "take_version": take_version,
            "prompt": prompt,
            "image_url": image_url,
            "source_url_kind": source_url_kind,
            "source_url_fast_path_expires_at": source_url_fast_path_expires_at,
            "source_asset_id": source_asset_id,
            "duration": payload.duration,
            "provider": "volcengine-ark",
        },
        label=f"{shot.code} · 第 {take_version} 版动态视频",
        stage="等待 Seedance 创建图生视频任务",
        trace_id=trace_id,
        estimated_seconds=180,
        max_attempts=3,
        retryable=True,
    )
    if replayed:
        return job_to_read(job), True
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="shot.video_generation_started",
        payload={"shot_id": shot.id, "take_version": take_version},
    )
    session.commit()
    session.refresh(job)
    return job_to_read(job), False


def materialize_generated_video(
    session: Session,
    *,
    job: Job,
    video: GeneratedVideo,
    settings: Settings,
) -> tuple[Asset, Take]:
    job_input = json.loads(job.input_json)
    take_version = int(job_input["take_version"])
    existing_take = session.scalar(
        select(Take).where(
            Take.shot_id == job.entity_id,
            Take.kind == "VIDEO",
            Take.version == take_version,
        )
    )
    if existing_take is not None:
        asset = session.get(Asset, existing_take.asset_id)
        if asset is None:
            raise RuntimeError("视频版本引用的资产不存在")
        return asset, existing_take

    digest = sha256(video.content).hexdigest()
    asset = session.scalar(
        select(Asset).where(
            Asset.project_id == job.project_id,
            Asset.sha256 == digest,
            Asset.kind == "SHOT_VIDEO",
        )
    )
    if asset is None:
        asset_id = str(uuid4())
        relative_path = Path("assets") / job.project_id / f"{asset_id}.mp4"
        destination = settings.data_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".mp4.tmp")
        temporary.write_bytes(video.content)
        temporary.replace(destination)
        asset = Asset(
            id=asset_id,
            project_id=job.project_id,
            kind="SHOT_VIDEO",
            storage_key=relative_path.as_posix(),
            sha256=digest,
            mime="video/mp4",
            size_bytes=len(video.content),
            status="READY",
            provider="volcengine-ark",
            is_temporary=True,
            width=None,
            height=None,
            duration_ms=video.duration_ms or int(job_input["duration"]) * 1000,
            source_entity_type="shot",
            source_entity_id=job.entity_id,
            created_at=datetime.now(UTC),
        )
        session.add(asset)
        session.flush()

    source_take = session.scalar(
        select(Take)
        .where(
            Take.shot_id == job.entity_id,
            Take.version == take_version,
            Take.kind != "VIDEO",
        )
        .order_by(Take.is_current.desc(), Take.created_at.desc())
    )
    shot = shot_or_404(session, job.entity_id)
    take = Take(
        id=str(uuid4()),
        shot_id=shot.id,
        kind="VIDEO",
        version=take_version,
        asset_id=asset.id,
        status="READY",
        approval="PENDING_REVIEW" if shot.candidate_take == take_version else "APPROVED",
        is_current=False,
        parent_take_id=source_take.id if source_take else None,
        created_at=datetime.now(UTC),
    )
    session.add(take)
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="shot.video_ready",
        payload={
            "shot_id": shot.id,
            "take_version": take_version,
            "video_take_id": take.id,
            "asset_id": asset.id,
        },
    )
    session.flush()
    return asset, take
