from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import ProposalGenerateRequest
from app.services.proposals import create_proposal_job, list_proposals

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
    job, replayed = create_proposal_job(
        session,
        project_id=project_id,
        expected_version=payload.expected_version,
        request_idempotency_key=idempotency_key,
        trace_id=get_trace_id(),
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(job)


@router.get("/projects/{project_id}/director-proposals")
def proposals(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_proposals(session, project_id))
