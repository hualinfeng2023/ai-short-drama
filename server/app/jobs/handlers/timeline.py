import asyncio
from functools import partial

from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.contracts import JobExecutionContext
from app.jobs.registry import register_job_handler
from app.services.exports import materialize_export
from app.services.media import build_preview_files
from app.services.revisions import register_revision_preview, revision_inputs


@register_job_handler("APPLY_REVISION")
async def apply_revision(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 15, "解析局部修改意图与显式依赖")
    project, episode, change_set, preview_shots, take_ids = revision_inputs(
        session,
        context.settings,
        job,
    )
    if change_set.result_timeline_id is not None:
        return {
            "change_set_id": change_set.id,
            "timeline_id": change_set.result_timeline_id,
            "provider": "mock",
            "replayed": True,
        }
    build = partial(
        build_preview_files,
        context.settings.data_dir / "tmp" / job.id / "revision-preview",
        project_id=project.id,
        project_name=project.name,
        aspect_ratio=project.aspect_ratio,
        shots=preview_shots,
    )
    media_task = asyncio.create_task(asyncio.to_thread(build))
    progress = 32
    while not media_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(media_task), timeout=2)
        except TimeoutError:
            progress = min(82, progress + 8)
            await context.checkpoint(
                session,
                job,
                progress,
                "仅重建受影响媒体并组装下一版时间线",
            )
            context.heartbeat(session, "RUNNING", job.id)
    files = await media_task
    await context.checkpoint(session, job, 88, "校验新旧版本与未受影响资产哈希")
    timeline = register_revision_preview(
        session,
        context.settings,
        job=job,
        episode=episode,
        change_set=change_set,
        preview_shots=preview_shots,
        take_ids=take_ids,
        files=files,
    )
    return {
        "change_set_id": change_set.id,
        "timeline_id": timeline.id,
        "timeline_version": timeline.version,
        "provider": "mock",
    }


@register_job_handler("EXPORT_PACKAGE")
async def export_package(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    _payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 24, "校验批准基线与演示权利声明")
    await context.checkpoint(session, job, 62, "打包 MP4、SRT、VTT 与来源清单")
    export = materialize_export(session, context.settings, job)
    await context.checkpoint(session, job, 92, "核验导出资产哈希与批准回链")
    return {
        "export_id": export.id,
        "timeline_id": export.timeline_id,
        "manifest_asset_id": export.manifest_asset_id,
        "rights_status": export.rights_status,
        "provider": "mock",
    }
