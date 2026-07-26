from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.models import Character, ProposalVersion, StoryVersion
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import CharacterLockRequest, ProposalApprovalRequest
from app.services.domain_commands import dispatch_domain_command
from app.services.production import (
    list_characters,
    list_previews,
)
from app.services.projects import content_hash
from app.services.workspace import project_or_404

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
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    proposal = session.scalar(
        select(ProposalVersion).where(
            ProposalVersion.project_id == project.id,
            ProposalVersion.version == proposal_version,
        )
    )
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROPOSAL_NOT_FOUND", "message": "导演方案版本不存在"},
        )
    fingerprint = content_hash(
        {
            "route": f"proposals:approve:{project.id}:{proposal.version}",
            "expected_version": payload.expected_version,
            "assumptions_confirmed": payload.assumptions_confirmed,
            "actor": payload.actor,
        }
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=str(
                uuid5(NAMESPACE_URL, f"{project.id}:APPROVE_PROPOSAL:{idempotency_key}")
            ),
            command_type="APPROVE_PROPOSAL",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=proposal.id,
            target_version_id=proposal.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=proposal.id,
            ),
            payload={
                "assumptions_confirmed": payload.assumptions_confirmed,
                "confirmed": True,
            },
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


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
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    story = (
        session.get(StoryVersion, project.current_story_version_id)
        if project.current_story_version_id
        else None
    )
    target_version_id = story.id if story is not None else project.id
    fingerprint = content_hash(
        {
            "route": f"characters:candidates:{project.id}",
            "story_version_id": target_version_id,
            "project_lock_version": project.lock_version,
        }
    )
    execution = dispatch_domain_command(
        session,
        project_id=project.id,
        command=DirectorCommand(
            command_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project.id}:REQUEST_CHARACTER_CANDIDATES:{idempotency_key}",
                )
            ),
            command_type="REQUEST_CHARACTER_CANDIDATES",
            actor=CommandActor(type="USER", id="demo-user"),
            target_object_id=target_version_id,
            target_version_id=target_version_id,
            expected_version=ExpectedVersion(
                project_lock_version=project.lock_version,
                target_version_id=target_version_id,
            ),
            payload={"confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.post(
    "/projects/{project_id}/characters/{character_id}/lock",
    status_code=status.HTTP_202_ACCEPTED,
)
def lock_character_candidate(
    project_id: str,
    character_id: str,
    payload: CharacterLockRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    actor: str = Header(default="demo-user", alias="X-Actor"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    character = session.get(Character, character_id)
    if character is None or character.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHARACTER_NOT_FOUND", "message": "角色不存在"},
        )
    fingerprint = content_hash(
        {
            "route": f"characters:lock:{project.id}:{character.id}",
            "candidate_id": payload.candidate_id,
            "expected_version": payload.expected_version,
            "actor": actor,
        }
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project.id}:LOCK_CHARACTER_CANDIDATE:{idempotency_key}",
                )
            ),
            command_type="LOCK_CHARACTER_CANDIDATE",
            actor=CommandActor(type="USER", id=actor),
            target_object_id=character.id,
            target_version_id=payload.candidate_id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=payload.candidate_id,
            ),
            payload={"candidate_id": payload.candidate_id, "confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/projects/{project_id}/previews")
def previews(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_previews(session, project_id))
