from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import StoryPackageGenerateRequest
from app.services.storyboards_v2 import (
    approve_storyboard,
    list_workflow_runs,
    storyboard_workspace,
)

router = APIRouter(prefix="/api/v1", tags=["storyboards"])


@router.get("/projects/{project_id}/storyboard-workspace")
def get_storyboard_workspace(
    project_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(storyboard_workspace(session, project_id))


@router.get("/projects/{project_id}/workflow-runs")
def workflow_runs(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_workflow_runs(session, project_id))


@router.post(
    "/storyboards/{storyboard_id}/approve",
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_storyboard_version(
    storyboard_id: str,
    payload: StoryPackageGenerateRequest,
    response: Response,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    storyboard, job, replayed = approve_storyboard(
        session,
        storyboard_id=storyboard_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
        trace_id=get_trace_id(),
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success({"storyboard": storyboard, "job": job})
