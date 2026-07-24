from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.config import Settings, get_settings
from app.db.models import (
    Character,
    CharacterIdentityVersion,
    CharacterVisualProfileVersion,
)
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import (
    CharacterCandidateDeleteRequest,
    CharacterCandidateGenerateRequest,
    CharacterCandidateSelectRequest,
    CharacterChangeApplyRequest,
    CharacterIdentityLockRequest,
    CharacterIdentityRestoreRequest,
    CharacterIdentityViewGenerateRequest,
    CharacterVisualProfileConfirmRequest,
    CharacterVisualProfileUpdateRequest,
)
from app.services.character_visuals import (
    apply_character_change,
    character_visual_workspace,
    delete_character_candidate,
    generate_character_candidates,
    generate_character_identity_view,
    select_character_candidate,
)
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash

router = APIRouter(prefix="/api/v1", tags=["character-visuals"])


def _dispatch_character_profile_command(
    session: Session,
    *,
    project_id: str,
    character_id: str,
    profile_version_id: str | None,
    expected_version: int,
    command_type: str,
    payload: dict[str, object],
    actor: str,
    idempotency_key: str | None,
) -> tuple[dict[str, object], bool]:
    character = session.get(Character, character_id)
    if character is None or character.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "角色不存在"},
        )
    target_version_id = profile_version_id or character.current_profile_version_id
    profile = session.get(CharacterVisualProfileVersion, target_version_id)
    if profile is None or profile.character_id != character.id:
        raise HTTPException(
            status_code=409,
            detail={"code": "PROFILE_NOT_READY", "message": "角色视觉档案尚未准备"},
        )
    command_id = (
        str(uuid5(NAMESPACE_URL, f"{project_id}:domain-command:{idempotency_key}"))
        if idempotency_key
        else str(uuid4())
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type=command_type,
            actor=CommandActor(type="USER", id=actor),
            target_object_id=character.id,
            target_version_id=profile.id,
            expected_version=ExpectedVersion(
                object_lock_version=expected_version,
                target_version_id=profile.id,
                target_hash=profile.content_hash,
            ),
            payload=payload,
            idempotency_key=idempotency_key or f"character-profile-adapter:{command_id}",
        ),
        request_fingerprint=content_hash(
            {
                "route": f"character-profile:{character_id}:{command_type}",
                "expected_version": expected_version,
                "payload": payload,
                "actor": actor,
            }
        ),
    )
    return execution.result, execution.idempotency_replayed


def _dispatch_character_identity_command(
    session: Session,
    *,
    project_id: str,
    character_id: str,
    identity_version_id: str,
    expected_version: int,
    command_type: str,
    actor: str,
    idempotency_key: str | None,
) -> tuple[dict[str, object], bool]:
    character = session.get(Character, character_id)
    identity = session.get(CharacterIdentityVersion, identity_version_id)
    if (
        character is None
        or character.project_id != project_id
        or identity is None
        or identity.character_id != character.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "角色身份版本不存在"},
        )
    command_id = (
        str(uuid5(NAMESPACE_URL, f"{project_id}:domain-command:{idempotency_key}"))
        if idempotency_key
        else str(uuid4())
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type=command_type,
            actor=CommandActor(type="USER", id=actor),
            target_object_id=character.id,
            target_version_id=identity.id,
            expected_version=ExpectedVersion(
                object_lock_version=expected_version,
                target_version_id=identity.id,
                target_hash=identity.content_hash,
            ),
            payload={
                "identity_version_id": identity.id,
                "confirmed": True,
            },
            idempotency_key=idempotency_key or f"character-identity-adapter:{command_id}",
        ),
        request_fingerprint=content_hash(
            {
                "route": f"character-identity:{character.id}:{command_type}",
                "identity_version_id": identity.id,
                "expected_version": expected_version,
                "actor": actor,
                "confirmed": True,
            }
        ),
    )
    return execution.result, execution.idempotency_replayed


@router.get("/projects/{project_id}/character-visuals")
def get_character_visuals(
    project_id: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(character_visual_workspace(session, project_id))


@router.patch("/projects/{project_id}/characters/{character_id}/visual-profile")
def patch_character_visual_profile(
    project_id: str,
    character_id: str,
    payload: CharacterVisualProfileUpdateRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    profile, replayed = _dispatch_character_profile_command(
        session,
        project_id=project_id,
        character_id=character_id,
        profile_version_id=None,
        expected_version=payload.expected_version,
        command_type="UPDATE_CHARACTER_VISUAL_PROFILE",
        payload={
            "changes": payload.model_dump(
                exclude={"expected_version", "actor"},
                exclude_none=True,
            )
        },
        actor=payload.actor,
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(profile)


@router.post("/projects/{project_id}/characters/{character_id}/visual-profile/confirm")
def confirm_character_visual_profile(
    project_id: str,
    character_id: str,
    payload: CharacterVisualProfileConfirmRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    profile, replayed = _dispatch_character_profile_command(
        session,
        project_id=project_id,
        character_id=character_id,
        profile_version_id=payload.profile_version_id,
        expected_version=payload.expected_version,
        command_type="CONFIRM_CHARACTER_VISUAL_PROFILE",
        payload={"profile_version_id": payload.profile_version_id},
        actor=payload.actor,
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(profile)


@router.post(
    "/projects/{project_id}/characters/{character_id}/visual-candidates",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_character_visual_candidates(
    project_id: str,
    character_id: str,
    payload: CharacterCandidateGenerateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    batch, jobs = generate_character_candidates(
        session,
        project_id=project_id,
        character_id=character_id,
        profile_version_id=payload.profile_version_id,
        expected_version=payload.expected_version,
        count=payload.count,
        source_candidate_id=payload.source_candidate_id,
        refinement_note=payload.refinement_note,
        custom_prompt=payload.custom_prompt,
        actor=payload.actor,
        trace_id=get_trace_id(),
    )
    return success({"batch": batch, "jobs": [item.model_dump(mode="json") for item in jobs]})


@router.delete(
    "/projects/{project_id}/characters/{character_id}/visual-candidates/{candidate_id}",
)
def delete_visual_candidate(
    project_id: str,
    character_id: str,
    candidate_id: str,
    payload: CharacterCandidateDeleteRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    return success(
        delete_character_candidate(
            session,
            settings,
            project_id=project_id,
            character_id=character_id,
            candidate_id=candidate_id,
            expected_version=payload.expected_version,
            actor=payload.actor,
        )
    )


@router.post(
    "/projects/{project_id}/characters/{character_id}/visual-candidates/select",
    status_code=status.HTTP_202_ACCEPTED,
)
def select_visual_candidate(
    project_id: str,
    character_id: str,
    payload: CharacterCandidateSelectRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    identity, jobs = select_character_candidate(
        session,
        project_id=project_id,
        character_id=character_id,
        candidate_id=payload.candidate_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
        trace_id=get_trace_id(),
    )
    return success({"identity": identity, "jobs": [item.model_dump(mode="json") for item in jobs]})


@router.post("/projects/{project_id}/characters/{character_id}/identity/lock")
def lock_visual_identity(
    project_id: str,
    character_id: str,
    payload: CharacterIdentityLockRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_character_identity_command(
        session,
        project_id=project_id,
        character_id=character_id,
        identity_version_id=payload.identity_version_id,
        expected_version=payload.expected_version,
        command_type="LOCK_CHARACTER_IDENTITY",
        actor=payload.actor,
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/projects/{project_id}/characters/{character_id}/identity/restore")
def restore_visual_identity(
    project_id: str,
    character_id: str,
    payload: CharacterIdentityRestoreRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_character_identity_command(
        session,
        project_id=project_id,
        character_id=character_id,
        identity_version_id=payload.identity_version_id,
        expected_version=payload.expected_version,
        command_type="RESTORE_CHARACTER_IDENTITY",
        actor=payload.actor,
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post(
    "/projects/{project_id}/characters/{character_id}/identity/{identity_version_id}/views",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_character_identity_view(
    project_id: str,
    character_id: str,
    identity_version_id: str,
    payload: CharacterIdentityViewGenerateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    job = generate_character_identity_view(
        session,
        project_id=project_id,
        character_id=character_id,
        identity_version_id=identity_version_id,
        view_type=payload.view_type,
        expected_version=payload.expected_version,
        refinement_note=payload.refinement_note,
        actor=payload.actor,
        trace_id=get_trace_id(),
    )
    return success({"job": job.model_dump(mode="json")})


@router.post("/projects/{project_id}/characters/{character_id}/changes")
def apply_visual_change(
    project_id: str,
    character_id: str,
    payload: CharacterChangeApplyRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result = apply_character_change(
        session,
        project_id=project_id,
        character_id=character_id,
        expected_version=payload.expected_version,
        change_type=payload.change_type,
        payload=payload.payload,
        decision=payload.decision,
        actor=payload.actor,
    )
    return success(result)
