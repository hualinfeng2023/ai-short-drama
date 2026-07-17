import asyncio

from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.contracts import JobExecutionContext
from app.jobs.registry import register_job_handler
from app.services.multitrack_timeline import assemble_multitrack_timeline


@register_job_handler("ASSEMBLE_MULTITRACK_TIMELINE")
async def assemble_multitrack(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 18, "创建视频、对白、背景音乐、环境音、音效与字幕轨")
    timeline = await asyncio.to_thread(
        assemble_multitrack_timeline,
        session,
        context.settings,
        job,
    )
    await context.checkpoint(session, job, 90, "完成整片质量检查并提交第 5 阶段画面锁定")
    return {
        "timeline_id": timeline.id,
        "timeline_version": timeline.version,
        "qc_report_asset_id": timeline.qc_report_asset_id,
        "stems_manifest_asset_id": timeline.stems_manifest_asset_id,
        "gate": "G5",
    }
