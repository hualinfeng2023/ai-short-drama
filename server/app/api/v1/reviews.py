from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.session import get_session
from app.schemas import GenericReviewDecisionRequest
from app.services.jobs import job_to_read
from app.services.media_production_v2 import decide_review, list_reviews

router = APIRouter(prefix="/api/v1", tags=["reviews"])


@router.get("/projects/{project_id}/reviews")
def project_reviews(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_reviews(session, project_id))


@router.post("/reviews/{review_id}/decide")
def review_decision(
    review_id: str,
    payload: GenericReviewDecisionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    review, job = decide_review(
        session,
        review_id=review_id,
        expected_version=payload.expected_version,
        decision=payload.decision,
        issues=payload.issues,
        note=payload.note,
        actor=payload.actor,
    )
    return success({"review": review, "job": job_to_read(job) if job else None})
