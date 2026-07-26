from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.models import Project
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import JobRecoveryRequest
from app.services.domain_commands import dispatch_domain_command
from app.services.jobs import (
    job_or_404,
    job_state_hash,
    job_to_read,
    list_jobs,
    list_project_jobs,
)
from app.services.projects import content_hash

router = APIRouter(prefix="/api/v1", tags=["jobs"])


def _dispatch_job_command(
    session: Session,
    *,
    job_id: str,
    command_type: str,
    payload: dict[str, object],
    actor: str,
    idempotency_key: str,
) -> tuple[dict[str, object], bool]:
    job = job_or_404(session, job_id)
    project = session.get(Project, job.project_id)
    if project is None:
        raise RuntimeError("任务所属项目不存在")
    command_id = str(
        uuid5(NAMESPACE_URL, f"{project.id}:domain-command:{idempotency_key}")
    )
    execution = dispatch_domain_command(
        session,
        project_id=project.id,
        command=DirectorCommand(
            command_id=command_id,
            command_type=command_type,
            actor=CommandActor(type="USER", id=actor),
            target_object_id=job.id,
            target_version_id=job.id,
            expected_version=ExpectedVersion(
                project_lock_version=project.lock_version,
                target_version_id=job.id,
                target_hash=job_state_hash(job),
            ),
            payload={**payload, "confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"job-operation:{job.id}:{command_type}",
                "payload": payload,
                "actor": actor,
                "confirmed": True,
            }
        ),
    )
    return execution.result, execution.idempotency_replayed


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
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    actor: str = Header(default="创作者", alias="X-Actor", min_length=1, max_length=80),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_job_command(
        session,
        job_id=job_id,
        command_type="CANCEL_JOB",
        payload={},
        actor=actor,
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    actor: str = Header(default="创作者", alias="X-Actor", min_length=1, max_length=80),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_job_command(
        session,
        job_id=job_id,
        command_type="RETRY_JOB",
        payload={},
        actor=actor,
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/jobs/{job_id}/recovery")
def recover_job(
    job_id: str,
    request: JobRecoveryRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    actor: str = Header(default="创作者", alias="X-Actor", min_length=1, max_length=80),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_job_command(
        session,
        job_id=job_id,
        command_type="RECOVER_JOB",
        payload=request.model_dump(mode="json"),
        actor=actor,
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)
