from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.session import get_session
from app.services.jobs import (
    job_or_404,
    job_to_read,
    list_jobs,
    list_project_jobs,
    request_cancel,
    request_retry,
)

router = APIRouter(prefix="/api/v1", tags=["jobs"])


@router.get("/projects/{project_id}/jobs")
def project_jobs(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_project_jobs(session, project_id))


@router.get("/jobs")
def jobs(session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_jobs(session))


@router.get("/jobs/{job_id}")
def job(job_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(job_to_read(job_or_404(session, job_id)))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(request_cancel(session, job_id))


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(request_retry(session, job_id))
