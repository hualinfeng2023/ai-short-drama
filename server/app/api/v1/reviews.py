from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.models import ReviewRecord
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import GenericReviewDecisionRequest
from app.services.domain_commands import dispatch_domain_command
from app.services.media_production_v2 import list_reviews
from app.services.projects import content_hash

router = APIRouter(prefix="/api/v1", tags=["reviews"])


@router.get("/projects/{project_id}/reviews")
def project_reviews(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_reviews(session, project_id))


@router.post("/reviews/{review_id}/decide")
def review_decision(
    review_id: str,
    payload: GenericReviewDecisionRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    review = session.get(ReviewRecord, review_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "REVIEW_NOT_FOUND", "message": "审核记录不存在"},
        )
    fingerprint = content_hash(
        {
            "route": f"reviews:decide:{review.id}",
            "expected_version": payload.expected_version,
            "decision": payload.decision,
            "issues": payload.issues,
            "note": payload.note,
            "actor": payload.actor,
        }
    )
    effective_key = idempotency_key or f"review-decision-{fingerprint}"
    execution = dispatch_domain_command(
        session,
        project_id=review.project_id,
        command=DirectorCommand(
            command_id=str(
                uuid5(NAMESPACE_URL, f"{review.project_id}:DECIDE_REVIEW:{effective_key}")
            ),
            command_type="DECIDE_REVIEW",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=review.id,
            target_version_id=review.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=review.id,
            ),
            payload={
                "decision": payload.decision,
                "issues": payload.issues,
                "note": payload.note,
                "confirmed": True,
            },
            idempotency_key=effective_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)
