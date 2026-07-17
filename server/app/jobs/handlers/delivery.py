import asyncio

from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.contracts import JobExecutionContext
from app.jobs.registry import register_job_handler
from app.services.delivery import materialize_export_v2


@register_job_handler("EXPORT_PACKAGE_V2")
async def export_package_v2(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 20, "验证第 5 阶段、整片质量检查、音频授权与导出规格")
    export = await asyncio.to_thread(
        materialize_export_v2,
        session,
        context.settings,
        job,
    )
    await context.checkpoint(session, job, 90, "登记画面母版复用与语言独立交付资产")
    return {
        "export_id": export.id,
        "profile_id": export.export_profile_id,
        "language": export.language,
        "picture_master_asset_id": export.picture_master_asset_id,
        "manifest_asset_id": export.manifest_asset_id,
        "rights_status": export.rights_status,
    }
