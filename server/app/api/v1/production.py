from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import CharacterLockRequest, ProposalApprovalRequest
from app.services.production import (
    approve_proposal,
    list_characters,
    list_previews,
    lock_character,
    request_character_candidates,
)

router = APIRouter(prefix="/api/v1", tags=["production"])


@router.post(
    "/projects/{project_id}/director-proposals/{proposal_version}/approve",
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_director_proposal(
    project_id: str,
    proposal_version: int,
    payload: ProposalApprovalRequest,
    response: Response,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    story, job, replayed = approve_proposal(
        session,
        project_id=project_id,
        proposal_version=proposal_version,
        expected_version=payload.expected_version,
        assumptions_confirmed=payload.assumptions_confirmed,
        actor=payload.actor,
        trace_id=get_trace_id(),
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success({"story": story, "job": job})


@router.get("/projects/{project_id}/characters/candidates")
def characters(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_characters(session, project_id))


@router.post(
    "/projects/{project_id}/characters/candidates",
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_characters(
    project_id: str,
    response: Response,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    job, replayed = request_character_candidates(
        session, project_id=project_id, trace_id=get_trace_id()
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(job)


@router.post(
    "/projects/{project_id}/characters/{character_id}/lock",
    status_code=status.HTTP_202_ACCEPTED,
)
def lock_character_candidate(
    project_id: str,
    character_id: str,
    payload: CharacterLockRequest,
    response: Response,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    actor: str = Header(default="demo-user", alias="X-Actor"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    character, job, replayed = lock_character(
        session,
        project_id=project_id,
        character_id=character_id,
        candidate_id=payload.candidate_id,
        expected_version=payload.expected_version,
        actor=actor,
        trace_id=get_trace_id(),
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success({"character": character, "job": job})


@router.get("/projects/{project_id}/previews")
def previews(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_previews(session, project_id))
