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
    for index in range(steps):
        progress = ((index + 1) / steps) * 90
        await context.checkpoint(session, job, progress, f"模拟渲染 {index + 1}/{steps}")
    return {"rendered": True, "provider": "mock"}
