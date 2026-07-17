import asyncio
import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Asset, Job, Project
from app.jobs.contracts import JobCancelled, JobExecutionContext, JobExecutionError
from app.jobs.registry import register_job_handler
from app.services.assets import resolve_asset_path
from app.services.media_production_v2 import (
    build_static_motion_video,
    elapsed_ms,
    generation_started_at,
    materialize_video_v2,
)
from app.services.media_staging import (
    MediaStagingError,
    StagedMedia,
    delete_staged_media,
    media_staging_enabled,
    seedream_fast_path_usable,
    stage_asset_for_seedance,
)
from app.services.projects import canonical_json
from app.services.video_provider import VideoProviderError
from app.services.videos import materialize_generated_video


def _merge_job_output(session: Session, job: Job, patch: dict[str, object]) -> None:
    current = json.loads(job.output_json) if job.output_json else {}
    if not isinstance(current, dict):
        current = {}
    current.update(patch)
    job.output_json = canonical_json(current)
    session.commit()


async def _prepare_seedance_source(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    source_asset: Asset | None,
    source_url: object,
    source_url_kind: object,
    source_url_fast_path_expires_at: object,
) -> tuple[str | None, StagedMedia | None]:
    if source_url_kind == "seedream-original" and seedream_fast_path_usable(
        source_url,
        source_url_fast_path_expires_at,
    ):
        await context.checkpoint(session, job, 16, "使用短时有效的 Seedream 原始 URL")
        return str(source_url), None
    if media_staging_enabled(context.settings) and source_asset is not None:
        await context.checkpoint(session, job, 16, "上传批准关键帧到私有 TOS")
        try:
            source_path = resolve_asset_path(context.settings, source_asset)
            staged = await asyncio.to_thread(
                stage_asset_for_seedance,
                context.settings,
                asset=source_asset,
                source=source_path,
                job_id=job.id,
            )
        except HTTPException as exc:
            raise JobExecutionError(
                "TOS_MEDIA_SOURCE_MISSING",
                "待暂存的关键帧文件不存在",
                retryable=False,
            ) from exc
        except MediaStagingError as exc:
            raise JobExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
        _merge_job_output(session, job, {"media_staging": staged.audit_metadata()})
        return staged.signed_url, staged
    if source_url_kind == "seedream-original":
        return None, None
    if isinstance(source_url, str) and source_url.startswith("https://"):
        return source_url, None
    return None, None


@register_job_handler("GENERATE_SHOT_VIDEO")
async def generate_shot_video(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 12, "创建或恢复 Seedance 图生视频任务")
    partial_output = json.loads(job.output_json) if job.output_json else {}
    if not isinstance(partial_output, dict):
        partial_output = {}
    stored_task_id = partial_output.get("provider_task_id")
    provider_task_id = stored_task_id if isinstance(stored_task_id, str) else None
    source_asset_id = payload.get("source_asset_id")
    source_asset = session.get(Asset, source_asset_id) if isinstance(source_asset_id, str) else None
    staged: StagedMedia | None = None
    media_staging_metadata: dict[str, object] | None = None
    if provider_task_id is not None:
        effective_source_url = (
            str(payload["image_url"])
            if isinstance(payload.get("image_url"), str)
            else "https://resume.invalid/source"
        )
    else:
        effective_source_url, staged = await _prepare_seedance_source(
            context,
            session,
            job,
            source_asset,
            payload.get("image_url"),
            payload.get("source_url_kind"),
            payload.get("source_url_fast_path_expires_at"),
        )
        if staged is not None:
            media_staging_metadata = staged.audit_metadata()
    if effective_source_url is None:
        raise JobExecutionError(
            "PUBLIC_IMAGE_URL_REQUIRED",
            "图生视频需要可暂存的本地关键帧或公网 HTTPS 图片",
            retryable=False,
        )
    progress = 18.0

    async def remember_task_id(task_id: str) -> None:
        nonlocal provider_task_id
        provider_task_id = task_id
        _merge_job_output(session, job, {"provider_task_id": task_id})

    async def report_provider_status(provider_status: str) -> None:
        nonlocal progress
        progress = min(78, progress + 3)
        label = "排队" if provider_status == "queued" else "生成"
        await context.checkpoint(session, job, progress, f"Seedance 正在{label}视频")
        context.heartbeat(session, "RUNNING", job.id)

    try:
        try:
            video = await context.generate_video(
                context.settings,
                prompt=str(payload["prompt"]),
                image_url=effective_source_url,
                provider_task_id=provider_task_id,
                on_task_created=remember_task_id,
                on_poll=report_provider_status,
            )
        except JobCancelled:
            if provider_task_id is not None:
                await context.cancel_video_task(context.settings, provider_task_id)
            raise
        except VideoProviderError as exc:
            raise JobExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
    finally:
        if staged is not None and media_staging_metadata is not None:
            if not context.settings.tos_cleanup_on_completion:
                media_staging_metadata["cleanup_status"] = "RETAINED_BY_CONFIG"
            else:
                deleted = await asyncio.to_thread(
                    delete_staged_media,
                    context.settings,
                    staged,
                )
                media_staging_metadata["cleanup_status"] = "DELETED" if deleted else "DELETE_FAILED"

    await context.checkpoint(session, job, 86, "保存视频资产并关联源素材版本")
    asset, take = materialize_generated_video(
        session,
        job=job,
        video=video,
        settings=context.settings,
    )
    return {
        "asset_id": asset.id,
        "take_id": take.id,
        "take_version": take.version,
        "provider": "volcengine-ark",
        "provider_task_id": video.provider_task_id,
        "model": video.model,
        "request_id": video.request_id,
        "source_url": video.source_url,
        "media_staging": media_staging_metadata,
    }


@register_job_handler("GENERATE_VIDEO_TAKE_V2")
async def generate_video_take_v2(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 12, "解析关键帧媒体可达性与视频策略")
    source_asset = session.get(Asset, str(payload["source_asset_id"]))
    project = session.get(Project, job.project_id)
    if source_asset is None or project is None:
        raise JobExecutionError(
            "VIDEO_SOURCE_MISSING",
            "正式视频缺少源关键帧或项目",
            retryable=False,
        )
    source_url = payload.get("source_url")
    started = generation_started_at()
    degraded_reason: str | None = None
    partial_output = json.loads(job.output_json) if job.output_json else {}
    if not isinstance(partial_output, dict):
        partial_output = {}
    stored_task_id = partial_output.get("provider_task_id")
    provider_task_id = stored_task_id if isinstance(stored_task_id, str) else None
    staged: StagedMedia | None = None
    media_staging_metadata: dict[str, object] | None = None
    effective_source_url: str | None = None
    if context.settings.ark_api_key:
        if provider_task_id is not None:
            effective_source_url = (
                source_url
                if isinstance(source_url, str) and source_url.startswith("https://")
                else "https://resume.invalid/source"
            )
        else:
            effective_source_url, staged = await _prepare_seedance_source(
                context,
                session,
                job,
                source_asset,
                source_url,
                payload.get("source_url_kind", "seedream-original"),
                payload.get("source_url_fast_path_expires_at"),
            )
            if staged is not None:
                media_staging_metadata = staged.audit_metadata()

    if context.settings.ark_api_key and effective_source_url is not None:
        stored_task_id = partial_output.get("provider_task_id")
        provider_task_id = stored_task_id if isinstance(stored_task_id, str) else None
        progress = 18.0

        async def remember_task_id(task_id: str) -> None:
            nonlocal provider_task_id
            provider_task_id = task_id
            _merge_job_output(session, job, {"provider_task_id": task_id})

        async def report_provider_status(provider_status: str) -> None:
            nonlocal progress
            progress = min(78, progress + 3)
            await context.checkpoint(
                session,
                job,
                progress,
                f"Seedance 正在{('排队' if provider_status == 'queued' else '生成')}视频",
            )
            context.heartbeat(session, "RUNNING", job.id)

        try:
            try:
                video = await context.generate_video(
                    context.settings,
                    prompt=str(payload["prompt"]),
                    image_url=effective_source_url,
                    provider_task_id=provider_task_id,
                    on_task_created=remember_task_id,
                    on_poll=report_provider_status,
                )
            except JobCancelled:
                if provider_task_id is not None:
                    await context.cancel_video_task(context.settings, provider_task_id)
                raise
            except VideoProviderError as exc:
                raise JobExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
        finally:
            if staged is not None and media_staging_metadata is not None:
                if not context.settings.tos_cleanup_on_completion:
                    media_staging_metadata["cleanup_status"] = "RETAINED_BY_CONFIG"
                else:
                    deleted = await asyncio.to_thread(
                        delete_staged_media,
                        context.settings,
                        staged,
                    )
                    media_staging_metadata["cleanup_status"] = (
                        "DELETED" if deleted else "DELETE_FAILED"
                    )
        provider = "volcengine-ark"
    else:
        degraded_reason = (
            "BLOCKED_BY_MEDIA_STAGING" if context.settings.ark_api_key else "MOCK_STATIC_MOTION"
        )
        output = context.settings.data_dir / "tmp" / job.id / "video-fallback" / "shot.mp4"
        video = await asyncio.to_thread(
            build_static_motion_video,
            context.settings,
            asset=source_asset,
            output=output,
            duration_sec=int(payload["duration"]),
            aspect_ratio=project.aspect_ratio,
        )
        provider = "static-fallback"
    await context.checkpoint(session, job, 86, "登记视频、技术质量检查与显式降级证据")
    asset, take, next_job = materialize_video_v2(
        session,
        context.settings,
        job,
        video,
        provider=provider,
        latency_ms=elapsed_ms(started),
        degraded_reason=degraded_reason,
        media_staging_metadata=media_staging_metadata,
    )
    return {
        "asset_id": asset.id,
        "take_id": take.id,
        "provider": provider,
        "provider_task_id": video.provider_task_id,
        "degraded_reason": degraded_reason,
        "media_staging": media_staging_metadata,
        "next_job_id": next_job.id if next_job else None,
    }
