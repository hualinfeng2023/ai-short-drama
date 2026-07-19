import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial

from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.contracts import JobExecutionContext, JobExecutionError
from app.jobs.registry import register_job_handler
from app.services.character_visuals import (
    CHARACTER_CLEAN_FRAME_CONSTRAINT,
    IDENTITY_DOSSIER_MAX_WAIT_SECONDS,
    materialize_identity_asset,
    materialize_visual_candidate,
)
from app.services.image_provider import ImageProviderError
from app.services.media import build_preview_files
from app.services.media_production_v2 import (
    elapsed_ms,
    generation_started_at,
    materialize_keyframe,
    start_media_production,
)
from app.services.media_staging import seedream_fast_path_expires_at
from app.services.preproduction import (
    materialize_character_candidate,
    materialize_character_looks,
    prepare_preproduction,
)
from app.services.production import (
    enqueue_preview_after_hero_fallback,
    materialize_character_candidates,
    materialize_storyboards,
    preview_inputs,
    register_preview,
)
from app.services.storyboards_v2 import (
    animatic_inputs,
    create_dynamic_storyboard,
    materialize_storyboard_take,
    reference_data_urls,
    register_animatic,
)


async def _generate_character_image(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
    *,
    reference_asset_ids: list[str],
    max_wait_seconds: int | None = None,
):
    references = reference_data_urls(
        session,
        context.settings,
        reference_asset_ids,
        mask_character_watermark=True,
    )
    prompt = (
        f"{payload['prompt']}\n"
        "参考图只用于人物身份一致性，不得把参考图中的非人物元素带入结果图。\n"
        f"{CHARACTER_CLEAN_FRAME_CONSTRAINT}。"
    )
    deadline = (
        asyncio.get_running_loop().time() + max_wait_seconds
        if max_wait_seconds is not None
        else None
    )
    generation = asyncio.create_task(
        context.generate_image(
            context.settings,
            prompt,
            model=context.settings.ark_image_model,
            size="2K",
            reference_images=references,
            seed=int(payload["seed"]),
        )
    )
    try:
        while not generation.done():
            remaining = deadline - asyncio.get_running_loop().time() if deadline else None
            if remaining is not None and remaining <= 0:
                raise JobExecutionError(
                    "CHARACTER_IMAGE_TIMEOUT",
                    f"角色身份图片生成超过 {max_wait_seconds} 秒",
                    retryable=False,
                    details={"max_wait_seconds": max_wait_seconds},
                )
            done, _pending = await asyncio.wait(
                {generation},
                timeout=min(4, remaining) if remaining is not None else 4,
            )
            if done:
                break
            await context.checkpoint(session, job, 58, "正在生成角色视觉资产")
            context.heartbeat(session, "RUNNING", job.id)
        try:
            return await generation
        except ImageProviderError as exc:
            raise JobExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
    finally:
        if not generation.done():
            generation.cancel()
            with suppress(asyncio.CancelledError):
                await generation


@register_job_handler("GENERATE_CHARACTER_CANDIDATES")
async def generate_character_candidates(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    if payload.get("script_version_id"):
        await context.checkpoint(session, job, 20, "从批准剧本提取全部角色、场景、道具与声音合同")
        child_job_ids = prepare_preproduction(session, job)
        await context.checkpoint(session, job, 90, "角色候选图片任务已展开")
        return {"child_job_ids": child_job_ids, "provider": "job-fan-out"}
    await context.checkpoint(session, job, 25, "生成两个确定性角色候选")
    candidate_ids = materialize_character_candidates(session, context.settings, job)
    await context.checkpoint(session, job, 90, "登记候选资产与生成证据")
    return {"candidate_ids": candidate_ids, "provider": "mock"}


@register_job_handler("PREPARE_PREPRODUCTION_ASSETS")
async def prepare_preproduction_assets(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 20, "从批准剧本提取场景、道具与声音合同")
    child_job_ids = prepare_preproduction(session, job)
    await context.checkpoint(session, job, 90, "前期资产已登记，锁定角色身份引用保持不变")
    return {"child_job_ids": child_job_ids, "provider": "structured-extraction"}


@register_job_handler("GENERATE_CHARACTER_CANDIDATE")
async def generate_character_candidate(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 12, "组装角色设定图提示词")
    generation = asyncio.create_task(
        context.generate_image(
            context.settings,
            str(payload["prompt"]),
            model=context.settings.ark_image_model,
            size="2K",
            reference_images=[],
            seed=int(payload["seed"]),
        )
    )
    try:
        while not generation.done():
            done, _pending = await asyncio.wait({generation}, timeout=4)
            if done:
                break
            await context.checkpoint(session, job, 58, "正在生成角色候选图")
            context.heartbeat(session, "RUNNING", job.id)
        try:
            image = await generation
        except ImageProviderError as exc:
            raise JobExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
    finally:
        if not generation.done():
            generation.cancel()
    await context.checkpoint(session, job, 82, "登记角色候选资产")
    asset, candidate = materialize_character_candidate(
        session,
        context.settings,
        job,
        image,
    )
    return {
        "character_id": candidate.character_id,
        "candidate_id": candidate.id,
        "asset_id": asset.id,
        "provider": "volcengine-ark" if context.settings.ark_api_key else "mock",
        "model": image.model,
        "request_id": image.request_id,
    }


@register_job_handler("GENERATE_CHARACTER_VISUAL_CANDIDATE")
async def generate_character_visual_candidate(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 12, "按结构化角色字段组装统一胸像")
    image = await _generate_character_image(
        context,
        session,
        job,
        payload,
        reference_asset_ids=[
            str(item)
            for item in payload.get("reference_asset_ids", [])
            if isinstance(item, str)
        ],
    )
    await context.checkpoint(session, job, 82, "登记角色形象候选与提示词快照")
    asset, candidate = materialize_visual_candidate(
        session,
        context.settings,
        job,
        image,
    )
    return {
        "character_id": candidate.character_id,
        "candidate_id": candidate.id,
        "asset_id": asset.id,
        "batch_id": candidate.batch_id,
        "provider": "volcengine-ark" if context.settings.ark_api_key else "mock",
        "model": image.model,
    }


@register_job_handler("GENERATE_CHARACTER_IDENTITY_DOSSIER")
async def generate_character_identity_dossier(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 12, "加载已选候选并锁定稳定身份特征")
    image = await _generate_character_image(
        context,
        session,
        job,
        payload,
        reference_asset_ids=[str(payload["reference_asset_id"])],
        max_wait_seconds=IDENTITY_DOSSIER_MAX_WAIT_SECONDS,
    )
    await context.checkpoint(session, job, 82, "登记角色身份档案视图")
    asset, record = materialize_identity_asset(
        session,
        context.settings,
        job,
        image,
    )
    return {
        "character_id": record.character_id,
        "identity_version_id": record.identity_version_id,
        "view_type": record.view_type,
        "asset_id": asset.id,
        "provider": "volcengine-ark" if context.settings.ark_api_key else "mock",
        "model": image.model,
    }


@register_job_handler("GENERATE_CHARACTER_LOOKS")
async def generate_character_looks(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 35, "生成基础与情绪升级造型规范")
    look_ids = materialize_character_looks(session, job)
    await context.checkpoint(session, job, 90, "造型版本已登记")
    return {"look_ids": look_ids, "provider": "structured-mock"}


@register_job_handler("GENERATE_STORYBOARD_V2")
async def generate_storyboard_v2(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 20, "从批准剧本动态拆分镜头规格")
    storyboard, child_job_ids = create_dynamic_storyboard(session, job)
    await context.checkpoint(session, job, 90, "分镜拆分任务已登记到工作流依赖图")
    return {
        "storyboard_version_id": storyboard.id,
        "shot_count": len(child_job_ids),
        "child_job_ids": child_job_ids,
    }


@register_job_handler("GENERATE_STORYBOARD_TAKE")
async def generate_storyboard_take(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 12, "加载角色造型、场景与道具引用")
    reference_asset_ids = [
        item for item in payload.get("reference_asset_ids", []) if isinstance(item, str)
    ]
    references = reference_data_urls(session, context.settings, reference_asset_ids)
    generation = asyncio.create_task(
        context.generate_image(
            context.settings,
            str(payload["prompt"]),
            model=context.settings.ark_image_model,
            size="2K",
            reference_images=references,
            seed=int(payload["seed"]),
        )
    )
    try:
        while not generation.done():
            done, _pending = await asyncio.wait({generation}, timeout=4)
            if done:
                break
            await context.checkpoint(session, job, 58, "正在生成低成本分镜版本")
            context.heartbeat(session, "RUNNING", job.id)
        try:
            image = await generation
        except ImageProviderError as exc:
            raise JobExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
    finally:
        if not generation.done():
            generation.cancel()
    await context.checkpoint(session, job, 84, "登记分镜版本与依赖图节点")
    asset, take, next_job = materialize_storyboard_take(
        session,
        context.settings,
        job,
        image,
    )
    return {
        "asset_id": asset.id,
        "take_id": take.id,
        "next_job_id": next_job.id if next_job else None,
        "model": image.model,
    }


@register_job_handler("GENERATE_ANIMATIC")
async def generate_animatic(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 15, "读取动态分镜与临时音轨意图")
    project, storyboard, preview_shots = animatic_inputs(
        session,
        context.settings,
        job,
    )
    build = partial(
        build_preview_files,
        context.settings.data_dir / "tmp" / job.id / "animatic",
        project_id=project.id,
        project_name=project.name,
        aspect_ratio=project.aspect_ratio,
        shots=preview_shots,
        hero_evidence={
            "temporary_dialogue": True,
            "temporary_music": True,
            "label": "ANIMATIC_ONLY",
        },
    )
    media_task = asyncio.create_task(asyncio.to_thread(build))
    progress = 30
    while not media_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(media_task), timeout=2)
        except TimeoutError:
            progress = min(82, progress + 8)
            await context.checkpoint(session, job, progress, "FFmpeg 正在组装带临时声音的节奏样片")
            context.heartbeat(session, "RUNNING", job.id)
    files = await media_task
    await context.checkpoint(session, job, 88, "校验节奏样片时长、视频和临时音轨")
    asset = register_animatic(
        session,
        context.settings,
        job=job,
        storyboard=storyboard,
        files=files,
    )
    return {
        "storyboard_version_id": storyboard.id,
        "animatic_asset_id": asset.id,
        "duration_ms": files.duration_ms,
        "temporary_audio": True,
    }


@register_job_handler("START_MEDIA_PRODUCTION")
async def start_formal_media_production(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 20, "展开正式关键帧候选与预算")
    child_job_ids = start_media_production(session, job)
    await context.checkpoint(session, job, 90, "关键帧拆分任务已登记到工作流依赖图")
    return {"keyframe_job_ids": child_job_ids, "count": len(child_job_ids)}


@register_job_handler("GENERATE_KEYFRAME_TAKE")
async def generate_keyframe_take(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 12, "组装正式关键帧多参考输入")
    reference_asset_ids = [
        item for item in payload.get("reference_asset_ids", []) if isinstance(item, str)
    ]
    references = reference_data_urls(session, context.settings, reference_asset_ids)
    started = generation_started_at()
    generation = asyncio.create_task(
        context.generate_image(
            context.settings,
            str(payload["prompt"]),
            model=context.settings.ark_image_model,
            size="2K",
            reference_images=references,
            seed=int(payload["seed"]),
        )
    )
    try:
        while not generation.done():
            done, _pending = await asyncio.wait({generation}, timeout=4)
            if done:
                break
            await context.checkpoint(session, job, 58, "正在生成正式关键帧")
            context.heartbeat(session, "RUNNING", job.id)
        try:
            image = await generation
        except ImageProviderError as exc:
            raise JobExecutionError(exc.code, exc.message, retryable=exc.retryable) from exc
    finally:
        if not generation.done():
            generation.cancel()
    await context.checkpoint(session, job, 80, "执行通用图片质量检查与审核路由")
    asset, take, next_job = materialize_keyframe(
        session,
        context.settings,
        job,
        image,
        elapsed_ms(started),
    )
    source_url_fast_path_expires_at = (
        seedream_fast_path_expires_at(context.settings, issued_at=datetime.now(UTC)).isoformat()
        if image.source_url
        else None
    )
    return {
        "asset_id": asset.id,
        "take_id": take.id,
        "quality_status": take.quality_status,
        "approval": take.approval,
        "next_job_id": next_job.id if next_job else None,
        "model": image.model,
        "request_id": image.request_id,
        "source_url": image.source_url,
        "source_url_kind": "seedream-original" if image.source_url else None,
        "source_url_fast_path_expires_at": source_url_fast_path_expires_at,
    }


@register_job_handler("GENERATE_STORYBOARDS")
async def generate_storyboards(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 15, "将故事展开为三幕八镜的影片中间结构")
    next_job = materialize_storyboards(session, context.settings, job)
    await context.checkpoint(session, job, 90, "分镜与当前版本已登记")
    return {"next_job_id": next_job.id, "shot_count": 8, "provider": "mock"}


@register_job_handler("GENERATE_HERO_FIXTURE")
async def generate_hero_fixture(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 30, "注入主镜头首次失败")
    await context.checkpoint(session, job, 68, "验证失败不会覆盖分镜版本")
    next_job = enqueue_preview_after_hero_fallback(session, job)
    await context.checkpoint(session, job, 92, "降级为平移缩放效果，且时间线无缺口")
    return {
        "next_job_id": next_job.id,
        "hero_shot_count": 0,
        "fallback": "KEN_BURNS",
        "failure_plan": "HERO_VIDEO:S05:attempt1",
        "provider": "mock",
    }


@register_job_handler("ASSEMBLE_PREVIEW")
async def assemble_preview(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 15, "读取当前版本与时间线输入")
    project, episode, preview_shots = preview_inputs(session, context.settings, job)
    build = partial(
        build_preview_files,
        context.settings.data_dir / "tmp" / job.id / "preview",
        project_id=project.id,
        project_name=project.name,
        aspect_ratio=project.aspect_ratio,
        shots=preview_shots,
        hero_evidence=payload.get("hero_evidence"),
    )
    media_task = asyncio.create_task(asyncio.to_thread(build))
    progress = 30
    while not media_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(media_task), timeout=2)
        except TimeoutError:
            progress = min(80, progress + 8)
            await context.checkpoint(
                session,
                job,
                progress,
                "FFmpeg 组装 H.264/AAC 混合小样",
            )
            context.heartbeat(session, "RUNNING", job.id)
    files = await media_task
    await context.checkpoint(session, job, 88, "ffprobe 与时间线完整性校验")
    timeline = register_preview(
        session,
        context.settings,
        job=job,
        episode=episode,
        preview_shots=preview_shots,
        files=files,
    )
    return {
        "timeline_id": timeline.id,
        "timeline_version": timeline.version,
        "duration_ms": timeline.duration_ms,
        "provider": "mock",
    }
