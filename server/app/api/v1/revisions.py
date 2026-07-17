from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import (
    PreviewApprovalRequest,
    PreviewRollbackRequest,
    RevisionCreateRequest,
    RevisionImpactRequest,
)
from app.services.revisions import (
    analyze_revision,
    approve_timeline,
    compare_timelines,
    create_revision,
    get_timeline,
    revision_or_404,
    rollback_timeline,
)

router = APIRouter(prefix="/api/v1", tags=["revision"])


@router.get("/previews/{timeline_id}")
def preview(timeline_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(get_timeline(session, timeline_id))


@router.post("/previews/{timeline_id}/approve")
def approve_preview(
    timeline_id: str,
    payload: PreviewApprovalRequest,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        approve_timeline(
            session,
            timeline_id=timeline_id,
            expected_version=payload.expected_version,
            actor=payload.actor,
            trace_id=get_trace_id(),
        )
    )


@router.get("/previews/{left_id}/compare/{right_id}")
def compare_preview(
    left_id: str, right_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(compare_timelines(session, left_id, right_id))


@router.post("/projects/{project_id}/revision-impact")
def revision_impact(
    project_id: str,
    payload: RevisionImpactRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        analyze_revision(
            session,
            project_id=project_id,
            expected_version=payload.expected_version,
            scope=payload.scope,
            instruction=payload.instruction,
        )
    )


@router.post(
    "/projects/{project_id}/revisions",
    status_code=status.HTTP_202_ACCEPTED,
)
def start_revision(
    project_id: str,
    payload: RevisionCreateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    change_set, job, replayed = create_revision(
        session,
        project_id=project_id,
        expected_version=payload.expected_version,
        scope=payload.scope,
        instruction=payload.instruction,
        confirmed=payload.confirmed,
        idempotency_key=idempotency_key,
        trace_id=get_trace_id(),
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success({"revision": change_set, "job": job})


@router.get("/revisions/{change_set_id}")
def revision(change_set_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(revision_or_404(session, change_set_id))


@router.post("/previews/{timeline_id}/rollback")
def rollback_preview(
    timeline_id: str,
    payload: PreviewRollbackRequest,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        rollback_timeline(
            session,
            timeline_id=timeline_id,
            expected_version=payload.expected_version,
            actor=payload.actor,
            trace_id=get_trace_id(),
        )
    )
