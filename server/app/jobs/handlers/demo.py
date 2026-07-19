from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.contracts import JobExecutionContext
from app.jobs.registry import register_job_handler


@register_job_handler("DEMO_RENDER")
async def demo_render(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    steps = int(payload.get("steps", 4))
    recovery_action = payload.get("recovery_action")
    resumable = recovery_action in {"RESUME_FROM_FAILURE", "RETRY_FAILED_PARTS"}
    completed_steps = min(steps, int((job.progress / 90) * steps)) if resumable else 0
    for index in range(completed_steps, steps):
        progress = ((index + 1) / steps) * 90
        stage_prefix = "恢复渲染" if resumable else "模拟渲染"
        await context.checkpoint(session, job, progress, f"{stage_prefix} {index + 1}/{steps}")
    return {
        "rendered": True,
        "provider": "mock",
        "resumed_from_step": completed_steps if resumable else None,
    }
