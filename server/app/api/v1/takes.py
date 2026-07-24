from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.models import Take
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import (
    IdentityReviewRequest,
    LegacyIdentityReviewRequest,
    PromptEnhanceRequest,
    ShotCharacterBindingUpdate,
    ShotImageGenerateRequest,
    ShotVideoGenerateRequest,
)
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash
from app.services.prompt_enhancer import enhance_shot_description
from app.services.takes import (
    approve_candidate_identity,
    create_shot_image_job,
)
from app.services.videos import create_shot_video_job
from app.services.workspace import shot_or_404, shot_to_read

router = APIRouter(prefix="/api/v1", tags=["takes"])


def _candidate_take_target_id(session: Session, shot_id: str) -> str:
    shot = shot_or_404(session, shot_id)
    candidate = (
        session.scalar(
            select(Take).where(
                Take.shot_id == shot.id,
                Take.kind == "STILL",
                Take.version == shot.candidate_take,
            )
        )
        if shot.candidate_take is not None
        else None
    )
    return candidate.id if candidate is not None else shot.current_take_id or shot.id


def _dispatch_take_command(
    session: Session,
    *,
    shot_id: str,
    expected_version: int,
    command_type: str,
    payload: dict[str, object],
    actor: str,
    idempotency_key: str,
    fingerprint_expected_version: int | None,
) -> tuple[dict[str, object], bool]:
    shot = shot_or_404(session, shot_id)
    project_id = shot.scene.episode.project_id
    target_take_id = _candidate_take_target_id(session, shot.id)
    command_id = str(
        uuid5(NAMESPACE_URL, f"{project_id}:domain-command:{idempotency_key}")
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type=command_type,
            actor=CommandActor(type="USER", id=actor),
            target_object_id=shot.id,
            target_version_id=target_take_id,
            expected_version=ExpectedVersion(
                object_lock_version=expected_version,
                target_version_id=target_take_id,
            ),
            payload={**payload, "confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"shot-take:{shot.id}:{command_type}",
                "expected_version": fingerprint_expected_version,
                "payload": payload,
                "actor": actor,
                "confirmed": True,
            }
        ),
    )
    return execution.result, execution.idempotency_replayed


@router.post("/shots/{shot_id}/prompt-enhance")
async def enhance_prompt(
    shot_id: str,
    payload: PromptEnhanceRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result = await enhance_shot_description(
        session,
        shot_id=shot_id,
        description=payload.description,
    )
    return success(result)


@router.post("/shots/{shot_id}/takes", status_code=status.HTTP_202_ACCEPTED)
def generate_take(
    shot_id: str,
    payload: ShotImageGenerateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    job, replayed = create_shot_image_job(
        session,
        shot_id=shot_id,
        prompt=payload.prompt,
        model=payload.model,
        resolution=payload.resolution,
        aspect_ratio=payload.aspect_ratio,
        request_idempotency_key=idempotency_key,
        trace_id=get_trace_id(),
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(job)


@router.post("/shots/{shot_id}/video-takes", status_code=status.HTTP_202_ACCEPTED)
def generate_video_take(
    shot_id: str,
    payload: ShotVideoGenerateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    job, replayed = create_shot_video_job(
        session,
        shot_id=shot_id,
        payload=payload,
        request_idempotency_key=idempotency_key,
        trace_id=get_trace_id(),
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(job)


@router.post("/shots/{shot_id}/takes/candidate/apply")
def apply_take(
    shot_id: str,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    shot = shot_or_404(session, shot_id)
    result, replayed = _dispatch_take_command(
        session,
        shot_id=shot.id,
        expected_version=shot.lock_version,
        command_type="APPLY_SHOT_TAKE",
        payload={},
        actor="demo-user",
        idempotency_key=idempotency_key,
        fingerprint_expected_version=None,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.put("/shots/{shot_id}/character-bindings")
def update_character_bindings(
    shot_id: str,
    payload: ShotCharacterBindingUpdate,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    shot = shot_or_404(session, shot_id)
    project_id = shot.scene.episode.project_id
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
            command_type="SET_SHOT_CHARACTER_BINDINGS",
            actor=CommandActor(type="USER", id="demo-user"),
            target_object_id=shot.id,
            target_version_id=shot.id,
            expected_version=ExpectedVersion(
                object_lock_version=payload.expected_version,
                target_version_id=shot.id,
            ),
            payload=payload.model_dump(exclude={"expected_version"}),
            idempotency_key=idempotency_key or f"shot-bindings-adapter:{command_id}",
        ),
        request_fingerprint=content_hash(
            {
                "route": f"shot-character-bindings:{shot.id}",
                "expected_version": payload.expected_version,
                "payload": payload.model_dump(exclude={"expected_version"}),
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(
        execution.idempotency_replayed
    ).lower()
    return success(execution.result)


@router.post("/shots/{shot_id}/takes/candidate/identity-approve")
def approve_identity(
    shot_id: str,
    payload: LegacyIdentityReviewRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    shot = approve_candidate_identity(session, shot_id, actor=payload.actor)
    return success(shot_to_read(session, shot))


@router.post("/shots/{shot_id}/takes/candidate/review")
def review_identity(
    shot_id: str,
    payload: IdentityReviewRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_take_command(
        session,
        shot_id=shot_id,
        expected_version=payload.expected_version,
        command_type="REVIEW_SHOT_TAKE",
        payload=payload.model_dump(exclude={"expected_version", "actor"}),
        actor=payload.actor,
        idempotency_key=idempotency_key,
        fingerprint_expected_version=payload.expected_version,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)
