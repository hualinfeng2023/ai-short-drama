from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import StoryPackageGenerateRequest
from app.services.preproduction import approve_preproduction, preproduction_workspace

router = APIRouter(prefix="/api/v1", tags=["preproduction"])


@router.get("/projects/{project_id}/preproduction")
def get_preproduction(
    project_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(preproduction_workspace(session, project_id))


@router.post(
    "/projects/{project_id}/preproduction/approve",
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_visual_bible(
    project_id: str,
    payload: StoryPackageGenerateRequest,
    response: Response,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    visual_bible, job, replayed = approve_preproduction(
        session,
        project_id=project_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
        trace_id=get_trace_id(),
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success({"visual_bible": visual_bible, "job": job})
