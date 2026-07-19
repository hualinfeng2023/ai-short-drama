from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import (
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
    confirm_visual_profile,
    generate_character_candidates,
    generate_character_identity_view,
    lock_character_identity,
    restore_character_identity,
    select_character_candidate,
    update_visual_profile,
)

router = APIRouter(prefix="/api/v1", tags=["character-visuals"])


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
    session: Session = Depends(get_session),
) -> dict[str, object]:
    profile = update_visual_profile(
        session,
        project_id=project_id,
        character_id=character_id,
        expected_version=payload.expected_version,
        changes=payload.model_dump(
            exclude={"expected_version", "actor"},
            exclude_none=True,
        ),
        actor=payload.actor,
    )
    return success(profile)


@router.post("/projects/{project_id}/characters/{character_id}/visual-profile/confirm")
def confirm_character_visual_profile(
    project_id: str,
    character_id: str,
    payload: CharacterVisualProfileConfirmRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    profile = confirm_visual_profile(
        session,
        project_id=project_id,
        character_id=character_id,
        profile_version_id=payload.profile_version_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
    )
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
        actor=payload.actor,
        trace_id=get_trace_id(),
    )
    return success({"batch": batch, "jobs": [item.model_dump(mode="json") for item in jobs]})


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
    session: Session = Depends(get_session),
) -> dict[str, object]:
    identity, script_job = lock_character_identity(
        session,
        project_id=project_id,
        character_id=character_id,
        identity_version_id=payload.identity_version_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
        trace_id=get_trace_id(),
    )
    return success(
        {
            "identity": identity,
            "script_job": script_job.model_dump(mode="json") if script_job else None,
        }
    )


@router.post("/projects/{project_id}/characters/{character_id}/identity/restore")
def restore_visual_identity(
    project_id: str,
    character_id: str,
    payload: CharacterIdentityRestoreRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    identity = restore_character_identity(
        session,
        project_id=project_id,
        character_id=character_id,
        identity_version_id=payload.identity_version_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
    )
    return success(identity)


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
