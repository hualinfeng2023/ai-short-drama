import asyncio
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.contracts import JobExecutionContext, JobExecutionError
from app.jobs.registry import register_job_handler
from app.services.image_provider import ImageProviderError
from app.services.media_staging import seedream_fast_path_expires_at
from app.services.takes import materialize_generated_take, reference_image_data_urls


@register_job_handler("GENERATE_SHOT_IMAGE")
async def generate_shot_image(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 12, "组装镜头提示词")
    prompt = str(payload["prompt"])
    provider = str(payload.get("provider", "mock"))
    model = str(payload.get("model") or context.settings.ark_image_model)
    resolution = str(payload.get("resolution") or "2K")
    aspect_ratio = str(payload.get("aspect_ratio") or "9:16")
    reference_asset_ids = [
        item for item in payload.get("reference_asset_ids", []) if isinstance(item, str)
    ]
    reference_images = reference_image_data_urls(
        session,
        context.settings,
        reference_asset_ids,
    )
    seed = payload.get("seed")
    resolved_seed = seed if isinstance(seed, int) else None
    generation = asyncio.create_task(
        context.generate_image(
            context.settings,
            prompt,
            model=model,
            size=resolution,
            reference_images=reference_images,
            seed=resolved_seed,
        )
    )
    progress = 20.0
    try:
        while not generation.done():
            done, _pending = await asyncio.wait({generation}, timeout=4)
            if done:
                break
            progress = min(72, progress + 4)
            stage = (
                f"Seedream 正在生成 {resolution} · {aspect_ratio} 图片"
                if provider == "volcengine-ark"
                else "确定性模拟服务正在生成关键帧"
            )
            await context.checkpoint(session, job, progress, stage)
            context.heartbeat(session, "RUNNING", job.id)
        try:
            image = await generation
        except ImageProviderError as exc:
            raise JobExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
    finally:
        if not generation.done():
            generation.cancel()

    await context.checkpoint(session, job, 76, "执行角色身份一致性检查")
    labels = [item for item in payload.get("character_labels", []) if isinstance(item, str)]
    identity_task = asyncio.create_task(
        context.evaluate_identity_consistency(
            context.settings,
            reference_images=reference_images,
            generated_image=image,
            character_labels=labels,
        )
    )
    try:
        while not identity_task.done():
            done, _pending = await asyncio.wait({identity_task}, timeout=4)
            if done:
                break
            await context.checkpoint(session, job, 80, "正在比对锁定角色参考图")
            context.heartbeat(session, "RUNNING", job.id)
        identity = await identity_task
    finally:
        if not identity_task.done():
            identity_task.cancel()

    await context.checkpoint(session, job, 86, "保存图片资产与身份审核结果")
    asset, take = materialize_generated_take(
        session,
        job=job,
        image=image,
        settings=context.settings,
        identity=identity,
    )
    source_url_fast_path_expires_at = (
        seedream_fast_path_expires_at(context.settings, issued_at=datetime.now(UTC)).isoformat()
        if image.source_url
        else None
    )
    return {
        "asset_id": asset.id,
        "take_id": take.id,
        "take_version": take.version,
        "provider": provider,
        "model": image.model,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "request_id": image.request_id,
        "source_url": image.source_url,
        "source_url_kind": "seedream-original" if image.source_url else None,
        "source_url_fast_path_expires_at": source_url_fast_path_expires_at,
        "seed": resolved_seed,
        "reference_asset_ids": reference_asset_ids,
        "identity_status": identity.status,
        "identity_score": identity.score,
        "identity_message": identity.message,
    }
