from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import (
    IdentityReviewRequest,
    LegacyIdentityReviewRequest,
    PromptEnhanceRequest,
    ShotCharacterBindingUpdate,
    ShotImageGenerateRequest,
    ShotVideoGenerateRequest,
)
from app.services.prompt_enhancer import enhance_shot_description
from app.services.takes import (
    apply_candidate_take,
    approve_candidate_identity,
    create_shot_image_job,
    review_candidate_identity,
    set_shot_character_bindings,
)
from app.services.videos import create_shot_video_job
from app.services.workspace import shot_to_read

router = APIRouter(prefix="/api/v1", tags=["takes"])


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
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(shot_to_read(session, apply_candidate_take(session, shot_id)))


@router.put("/shots/{shot_id}/character-bindings")
def update_character_bindings(
    shot_id: str,
    payload: ShotCharacterBindingUpdate,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    shot = set_shot_character_bindings(
        session,
        shot_id=shot_id,
        expected_version=payload.expected_version,
        character_ids=payload.character_ids,
        look_version=payload.look_version,
    )
    return success(shot_to_read(session, shot))


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
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    shot, job = review_candidate_identity(
        session,
        shot_id=shot_id,
        decision=payload.decision,
        issues=list(payload.issues),
        note=payload.note,
        expected_version=payload.expected_version,
        actor=payload.actor,
        request_idempotency_key=idempotency_key,
        trace_id=get_trace_id(),
    )
    return success(
        {
            "action": payload.decision,
            "shot": shot_to_read(session, shot),
            "job": job,
        }
    )
