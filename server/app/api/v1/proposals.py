from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.models import BriefVersion, Job
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import ProposalGenerateRequest
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash
from app.services.proposals import PROPOSAL_CONFIG_VERSION, list_proposals
from app.services.workspace import project_or_404

router = APIRouter(prefix="/api/v1", tags=["proposals"])


@router.post(
    "/projects/{project_id}/director-proposals",
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_proposal(
    project_id: str,
    payload: ProposalGenerateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    latest_brief = session.scalar(
        select(BriefVersion)
        .where(BriefVersion.project_id == project_id)
        .order_by(BriefVersion.version.desc())
    )
    target_version_id = latest_brief.id if latest_brief is not None else project.id
    business_key = (
        f"{project_id}:GENERATE_PROPOSAL:{project_id}:"
        f"brief-{latest_brief.version}:{PROPOSAL_CONFIG_VERSION}"
        if latest_brief is not None
        else ""
    )
    existing = (
        session.scalar(select(Job).where(Job.idempotency_key == business_key))
        if business_key
        else None
    )
    fingerprint = content_hash(
        {
            "route": f"director-proposals:generate:{project_id}",
            "expected_version": payload.expected_version,
            "brief_version_id": target_version_id,
        }
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project_id}:REQUEST_DIRECTOR_PROPOSAL_GENERATION:{idempotency_key}",
                )
            ),
            command_type="REQUEST_DIRECTOR_PROPOSAL_GENERATION",
            actor=CommandActor(type="USER", id="demo-user"),
            target_object_id=project_id,
            target_version_id=target_version_id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=target_version_id,
            ),
            payload={"confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(
        execution.idempotency_replayed or existing is not None
    ).lower()
    return success(execution.result)


@router.get("/projects/{project_id}/director-proposals")
def proposals(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_proposals(session, project_id))
