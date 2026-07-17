from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.contracts import JobExecutionContext
from app.jobs.registry import register_job_handler
from app.services.audio_pipeline import (
    create_audio_pipeline,
    create_lip_sync_batch,
    materialize_audio_take,
)


@register_job_handler("GENERATE_AUDIO_PIPELINE")
async def generate_audio_pipeline(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 20, "生成音乐与声音简报及逐轨音频节点")
    brief, child_ids = create_audio_pipeline(session, job)
    await context.checkpoint(session, job, 88, "登记对白、背景音乐、环境音与音效的任务依赖")
    return {"sound_brief_version_id": brief.id, "child_job_ids": child_ids}


@register_job_handler("GENERATE_AUDIO_TAKE")
async def generate_audio_take(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    cue_type = str(payload["cue_type"])
    cue_label = {
        "DIALOGUE": "对白",
        "BGM": "背景音乐",
        "AMBIENCE": "环境音",
        "SFX": "音效",
    }.get(cue_type, cue_type)
    await context.checkpoint(session, job, 30, f"生成{cue_label}音频")
    take, next_job = materialize_audio_take(session, context.settings, job)
    await context.checkpoint(session, job, 88, "执行时长、削波、响度与对白遮盖质量检查")
    return {
        "audio_take_id": take.id,
        "asset_id": take.asset_id,
        "quality_status": take.quality_status,
        "next_job_id": next_job.id if next_job else None,
    }


@register_job_handler("GENERATE_LIP_SYNC_BATCH")
async def generate_lip_sync_batch(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 35, "仅使用已批准的对白版本与视频版本")
    results, next_job = create_lip_sync_batch(session, job)
    await context.checkpoint(session, job, 88, "登记口型结果或画外音降级，不覆盖源视频")
    return {
        "lip_sync_take_ids": [item.id for item in results],
        "next_job_id": next_job.id,
    }
