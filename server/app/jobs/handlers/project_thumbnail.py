import asyncio

from sqlalchemy.orm import Session

from app.db.models import Job, Project, ScriptVersion
from app.jobs.contracts import JobExecutionContext, JobExecutionError
from app.jobs.registry import register_job_handler
from app.services.image_provider import ImageProviderError
from app.services.project_thumbnails import (
    latest_script_is_current,
    materialize_project_thumbnail,
    thumbnail_generation_resolution,
)


@register_job_handler("GENERATE_PROJECT_THUMBNAIL")
async def generate_project_thumbnail(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    project = session.get(Project, job.project_id)
    script_id = payload.get("script_version_id")
    script = session.get(ScriptVersion, script_id) if isinstance(script_id, str) else None
    if project is None or script is None or script.project_id != job.project_id:
        raise JobExecutionError(
            "PROJECT_THUMBNAIL_SOURCE_MISSING",
            "剧本缩略图的来源已不存在",
            retryable=False,
        )
    if (
        payload.get("script_content_hash") != script.content_hash
        or not latest_script_is_current(session, script)
    ):
        return {"skipped": True, "reason": "STALE_SCRIPT_VERSION", "script_version_id": script.id}

    width = int(payload["target_width"])
    height = int(payload["target_height"])
    seed = int(payload["seed"])
    resolution = thumbnail_generation_resolution(context.settings)
    await context.checkpoint(session, job, 15, "根据剧本提炼封面核心视觉")
    generation = asyncio.create_task(
        context.generate_image(
            context.settings,
            str(payload["prompt"]),
            model=context.settings.ark_image_model,
            size=resolution,
            reference_images=[],
            seed=seed,
        )
    )
    try:
        progress = 28.0
        while not generation.done():
            done, _pending = await asyncio.wait({generation}, timeout=3)
            if done:
                break
            progress = min(76, progress + 6)
            await context.checkpoint(
                session,
                job,
                progress,
                f"Seedream 正在生成 {resolution} 封面底图",
            )
            context.heartbeat(session, "RUNNING", job.id)
        try:
            image = await generation
        except ImageProviderError as exc:
            raise JobExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
    finally:
        if not generation.done():
            generation.cancel()

    await context.checkpoint(session, job, 84, f"输出 {width}×{height} 剧本缩略图")
    asset = materialize_project_thumbnail(
        session,
        settings=context.settings,
        job=job,
        project=project,
        script=script,
        content=image.content,
        model=image.model,
        request_id=image.request_id,
        generation_resolution=resolution,
        seed=seed,
    )
    return {
        "asset_id": asset.id,
        "script_version_id": script.id,
        "width": asset.width,
        "height": asset.height,
        "model": image.model,
        "generation_resolution": resolution,
    }
