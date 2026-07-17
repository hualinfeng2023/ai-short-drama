import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import (
    ARK_IMAGE_ASPECT_RATIOS,
    Settings,
    available_ark_image_models,
    available_ark_image_resolutions,
    get_settings,
)
from app.db.models import (
    Asset,
    Character,
    CharacterCandidate,
    Episode,
    Job,
    Project,
    Scene,
    Shot,
    Take,
)
from app.schemas import JobRead
from app.services.assets import resolve_asset_path
from app.services.events import append_event
from app.services.identity_consistency import IdentityEvaluation, image_data_url
from app.services.image_provider import GeneratedImage
from app.services.jobs import ACTIVE_STATUSES, QUEUED_STATUSES, enqueue_job, job_to_read
from app.services.workspace import shot_or_404

IMAGE_JOB_TYPE = "GENERATE_SHOT_IMAGE"

IDENTITY_ISSUE_PROMPTS = {
    "FACE_SHAPE": "脸型轮廓需要更贴近参考角色",
    "FACIAL_FEATURES": "五官比例和辨识特征需要更贴近参考角色",
    "HAIR": "发型、发际线和发色需要与参考角色一致",
    "AGE_IMPRESSION": "年龄感需要与参考角色一致",
    "WARDROBE": "服装造型需要符合当前 Look 版本",
    "BODY_PROPORTIONS": "身形和身体比例需要与参考角色一致",
    "SIGNATURE_ACCESSORIES": "标志性配饰需要保留并保持一致",
}


CharacterBinding = tuple[Character, CharacterCandidate, Asset]


def _shot_project(session: Session, shot: Shot) -> Project:
    project = session.scalar(
        select(Project)
        .join(Episode, Episode.project_id == Project.id)
        .join(Scene, Scene.episode_id == Episode.id)
        .where(Scene.id == shot.scene_id)
    )
    if project is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "SHOT_PROJECT_MISSING", "message": "镜头未关联到项目"},
        )
    return project


def build_shot_prompt(project: Project, shot: Shot, aspect_ratio: str | None = None) -> str:
    resolved_ratio = aspect_ratio or project.aspect_ratio
    orientation_label = {
        "1:1": "正方形",
        "4:3": "横向标准画幅",
        "3:4": "竖向标准画幅",
        "16:9": "横屏宽画幅",
        "9:16": "竖屏短视频画幅",
        "3:2": "横向摄影画幅",
        "2:3": "竖向摄影画幅",
        "21:9": "超宽银幕画幅",
    }.get(resolved_ratio, "指定画幅")
    orientation = f"{orientation_label} {resolved_ratio} 构图"
    dialogue_hint = f"，人物正在说：{shot.dialogue}" if shot.dialogue.strip() else ""
    return (
        f"{shot.description}{dialogue_hint}。地点：{shot.location}，时间：{shot.time_of_day}。"
        f"{shot.shot_size} 景别，{shot.camera_movement} 运镜，{orientation}。"
        f"整体风格：{project.style}，电影大片质感，真实光影，细腻丰富的色彩层次，"
        "明确主体，景深，动态构图，高细节，避免画面文字、字幕、边框和拼贴。"
    )


def _stored_character_ids(shot: Shot) -> list[str]:
    try:
        value = json.loads(shot.character_ids_json or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _character_bindings(
    session: Session,
    *,
    project: Project,
    shot: Shot,
    requested_character_ids: list[str] | None = None,
) -> list[CharacterBinding]:
    character_ids = list(dict.fromkeys(requested_character_ids or _stored_character_ids(shot)))
    if not character_ids:
        character_ids = list(
            session.scalars(
                select(Character.id)
                .where(
                    Character.project_id == project.id,
                    Character.locked_candidate_id.is_not(None),
                )
                .order_by(Character.role, Character.id)
            ).all()
        )
    if not character_ids:
        return []
    characters = list(
        session.scalars(
            select(Character).where(
                Character.id.in_(character_ids), Character.project_id == project.id
            )
        ).all()
    )
    by_id = {character.id: character for character in characters}
    if missing := [character_id for character_id in character_ids if character_id not in by_id]:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SHOT_CHARACTER_INVALID",
                "message": "分镜绑定了不属于当前项目的角色",
                "details": {"character_ids": missing},
            },
        )
    bindings: list[CharacterBinding] = []
    for character_id in character_ids:
        character = by_id[character_id]
        if not character.locked_candidate_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CHARACTER_REFERENCE_NOT_LOCKED",
                    "message": f"角色“{character.name}”尚未锁定参考形象",
                    "details": {"character_id": character.id},
                },
            )
        candidate = session.get(CharacterCandidate, character.locked_candidate_id)
        asset = session.get(Asset, candidate.asset_id) if candidate else None
        if candidate is None or candidate.character_id != character.id or asset is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CHARACTER_REFERENCE_MISSING",
                    "message": f"角色“{character.name}”的锁定参考资产不可用",
                    "details": {"character_id": character.id},
                },
            )
        bindings.append((character, candidate, asset))
    return bindings


def _identity_prompt(bindings: list[CharacterBinding], look_version: str) -> str:
    if not bindings:
        return ""
    references = "\n".join(
        f"- 参考图{index}：{character.name}（{character.role}）；{character.visual_brief}"
        for index, (character, _candidate, _asset) in enumerate(bindings, start=1)
    )
    return "\n".join(
        (
            "角色身份锁定（硬约束）：",
            references,
            f"- 当前造型版本：{look_version}。",
            "- 参考图是每个角色唯一的身份基准。必须保持同一人物的脸型、五官比例、"
            "发型核心特征、年龄感和辨识度；允许随剧情改变表情、姿势、景别、光线与背景。",
            "- 不得把不同参考图的五官混合，不得新增长相相似的替代人物；"
            "当前分镜未绑定的角色不要入镜。",
        )
    )


def reference_image_data_urls(
    session: Session, settings: Settings, reference_asset_ids: list[str]
) -> list[str]:
    images: list[str] = []
    for asset_id in reference_asset_ids:
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise RuntimeError(f"Character reference asset missing: {asset_id}")
        images.append(image_data_url(resolve_asset_path(settings, asset).read_bytes(), asset.mime))
    return images


def create_shot_image_job(
    session: Session,
    *,
    shot_id: str,
    prompt: str | None,
    model: str | None,
    resolution: str,
    aspect_ratio: str | None,
    request_idempotency_key: str,
    trace_id: str,
) -> tuple[JobRead, bool]:
    shot = shot_or_404(session, shot_id)
    project = _shot_project(session, shot)
    active = session.scalar(
        select(Job)
        .where(
            Job.job_type == IMAGE_JOB_TYPE,
            Job.entity_id == shot_id,
            Job.status.in_(QUEUED_STATUSES | ACTIVE_STATUSES),
        )
        .order_by(Job.created_at.desc())
    )
    if active is not None:
        return job_to_read(active), True

    next_take = (
        max(
            shot.current_take,
            shot.candidate_take or 0,
            session.scalar(select(func.max(Take.version)).where(Take.shot_id == shot_id)) or 0,
        )
        + 1
    )
    settings = get_settings()
    provider = "volcengine-ark" if settings.ark_api_key else "mock"
    available_models = {option["id"] for option in available_ark_image_models(settings)}
    requested_model = model.strip() if model and model.strip() else settings.ark_image_model
    if settings.ark_api_key and requested_model not in available_models:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNSUPPORTED_IMAGE_MODEL",
                "message": "所选生图模型不在服务端允许列表中",
                "details": {"model": requested_model, "available_models": sorted(available_models)},
            },
        )
    available_resolutions = available_ark_image_resolutions(requested_model)
    if settings.ark_api_key and resolution not in available_resolutions:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNSUPPORTED_IMAGE_RESOLUTION",
                "message": "所选分辨率不受当前生图模型支持",
                "details": {
                    "model": requested_model,
                    "resolution": resolution,
                    "available_resolutions": list(available_resolutions),
                },
            },
        )
    resolved_aspect_ratio = aspect_ratio or project.aspect_ratio
    if resolved_aspect_ratio not in ARK_IMAGE_ASPECT_RATIOS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNSUPPORTED_IMAGE_ASPECT_RATIO",
                "message": "所选画面比例不受支持",
                "details": {"aspect_ratio": resolved_aspect_ratio},
            },
        )
    base_prompt = (
        prompt.strip()
        if prompt and prompt.strip()
        else build_shot_prompt(project, shot, resolved_aspect_ratio)
    )
    bindings = _character_bindings(session, project=project, shot=shot)
    character_ids = [character.id for character, _candidate, _asset in bindings]
    reference_asset_ids = [asset.id for _character, _candidate, asset in bindings]
    if character_ids:
        shot.character_ids_json = json.dumps(character_ids)
    identity_block = _identity_prompt(bindings, shot.character_look_version)
    resolved_prompt = (
        f"{base_prompt.rstrip('。')}。{identity_block}\n严格使用 {resolved_aspect_ratio} 画面比例，"
        f"目标分辨率 {resolution}。"
    )
    seed_material = "|".join(
        [candidate.seed for _character, candidate, _asset in bindings]
        or [project.id, shot.id, shot.character_look_version]
    )
    stable_seed = int.from_bytes(sha256(seed_material.encode()).digest()[:4], "big") & 0x7FFFFFFF
    resolved_model = requested_model if settings.ark_api_key else "deterministic-image-v1"
    job, replayed = enqueue_job(
        session,
        project_id=project.id,
        job_type=IMAGE_JOB_TYPE,
        entity_type="shot",
        entity_id=shot.id,
        idempotency_key=f"shot-image:{shot.id}:{request_idempotency_key}",
        input_payload={
            "shot_id": shot.id,
            "take_version": next_take,
            "prompt": resolved_prompt,
            "provider": provider,
            "model": resolved_model,
            "resolution": resolution,
            "aspect_ratio": resolved_aspect_ratio,
            "character_ids": character_ids,
            "character_labels": [character.name for character, _candidate, _asset in bindings],
            "character_look_version": shot.character_look_version,
            "reference_asset_ids": reference_asset_ids,
            "seed": stable_seed,
        },
        label=f"{shot.code} · 素材第 {next_take} 版",
        stage="等待 Seedream 生成关键帧" if settings.ark_api_key else "等待模拟生成关键帧",
        trace_id=trace_id,
        estimated_seconds=45 if settings.ark_api_key else 2,
        max_attempts=3,
        retryable=True,
    )
    if replayed:
        return job_to_read(job), True

    shot.status = "GENERATING"
    shot.candidate_take = next_take
    shot.lock_version += 1
    project.status = "PRODUCING"
    project.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="shot.image_generation_started",
        payload={
            "shot_id": shot.id,
            "take_version": next_take,
            "model": resolved_model,
            "resolution": resolution,
            "aspect_ratio": resolved_aspect_ratio,
            "character_ids": character_ids,
            "character_look_version": shot.character_look_version,
            "reference_asset_ids": reference_asset_ids,
            "seed": stable_seed,
        },
    )
    session.commit()
    session.refresh(job)
    return job_to_read(job), False


def _extension_for_mime(mime: str) -> str:
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[mime]


def materialize_generated_take(
    session: Session,
    *,
    job: Job,
    image: GeneratedImage,
    settings: Settings,
    identity: IdentityEvaluation,
) -> tuple[Asset, Take]:
    input_payload = json.loads(job.input_json)
    take_version = int(input_payload["take_version"])
    reference_asset_ids = [
        item for item in input_payload.get("reference_asset_ids", []) if isinstance(item, str)
    ]
    existing_take = session.scalar(
        select(Take).where(
            Take.shot_id == job.entity_id,
            Take.kind == "STILL",
            Take.version == take_version,
        )
    )
    if existing_take is not None:
        asset = session.get(Asset, existing_take.asset_id)
        if asset is None:
            raise RuntimeError("素材版本引用的资产不存在")
        return asset, existing_take

    digest = sha256(image.content).hexdigest()
    asset = session.scalar(
        select(Asset).where(
            Asset.project_id == job.project_id,
            Asset.sha256 == digest,
            Asset.kind == "SHOT_IMAGE",
        )
    )
    if asset is None:
        asset_id = str(uuid4())
        filename = f"{asset_id}{_extension_for_mime(image.mime)}"
        relative_path = Path("assets") / job.project_id / filename
        destination = settings.data_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        temporary.write_bytes(image.content)
        temporary.replace(destination)
        provider = "mock" if image.model == "deterministic-image-v1" else "volcengine-ark"
        asset = Asset(
            id=asset_id,
            project_id=job.project_id,
            kind="SHOT_IMAGE",
            storage_key=relative_path.as_posix(),
            sha256=digest,
            mime=image.mime,
            size_bytes=len(image.content),
            status="READY",
            provider=provider,
            is_temporary=True,
            width=image.width,
            height=image.height,
            duration_ms=None,
            metadata_json=json.dumps(
                {
                    "generation": {
                        "model": image.model,
                        "request_id": image.request_id,
                        "seed": input_payload.get("seed"),
                        "reference_asset_ids": reference_asset_ids,
                    },
                    "identity_qc": {
                        "status": identity.status,
                        "score": identity.score,
                        "message": identity.message,
                        "provider": identity.provider,
                        "model": identity.model,
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            source_entity_type="shot",
            source_entity_id=job.entity_id,
            created_at=datetime.now(UTC),
        )
        session.add(asset)
        session.flush()

    shot = shot_or_404(session, job.entity_id)
    take = Take(
        id=str(uuid4()),
        shot_id=shot.id,
        kind="STILL",
        version=take_version,
        asset_id=asset.id,
        status="READY",
        approval="PENDING_REVIEW",
        is_current=False,
        parent_take_id=shot.current_take_id,
        identity_status=identity.status,
        identity_score=identity.score,
        identity_message=identity.message,
        identity_reference_asset_ids_json=json.dumps(reference_asset_ids),
        created_at=datetime.now(UTC),
    )
    session.add(take)
    shot.candidate_take = take_version
    shot.status = "PENDING_REVIEW"
    shot.lock_version += 1
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="shot.image_ready",
        payload={
            "shot_id": shot.id,
            "take_id": take.id,
            "take_version": take.version,
            "asset_id": asset.id,
            "identity_status": identity.status,
            "identity_score": identity.score,
        },
    )
    session.flush()
    return asset, take


def set_shot_character_bindings(
    session: Session,
    *,
    shot_id: str,
    expected_version: int,
    character_ids: list[str],
    look_version: str,
) -> Shot:
    shot = shot_or_404(session, shot_id)
    if shot.lock_version != expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "VERSION_CONFLICT",
                "message": "分镜已被其他操作更新，请刷新后重试",
                "details": {"expected": expected_version, "actual": shot.lock_version},
            },
        )
    project = _shot_project(session, shot)
    unique_ids = list(dict.fromkeys(character_ids))
    _character_bindings(
        session,
        project=project,
        shot=shot,
        requested_character_ids=unique_ids,
    ) if unique_ids else []
    shot.character_ids_json = json.dumps(unique_ids)
    shot.character_look_version = look_version
    shot.lock_version += 1
    project.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="shot.character_bindings_updated",
        payload={
            "shot_id": shot.id,
            "character_ids": unique_ids,
            "character_look_version": look_version,
        },
    )
    session.commit()
    session.refresh(shot)
    return shot


def approve_candidate_identity(session: Session, shot_id: str, *, actor: str) -> Shot:
    shot = shot_or_404(session, shot_id)
    if shot.candidate_take is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "CANDIDATE_TAKE_MISSING", "message": "当前没有候选版本"},
        )
    candidate = session.scalar(
        select(Take).where(
            Take.shot_id == shot.id,
            Take.kind == "STILL",
            Take.version == shot.candidate_take,
            Take.status == "READY",
        )
    )
    if candidate is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "CANDIDATE_TAKE_NOT_READY", "message": "候选版本尚未就绪"},
        )
    if candidate.identity_status != "NOT_APPLICABLE":
        candidate.identity_status = "PASSED"
        candidate.identity_message = f"已由 {actor} 人工确认角色身份一致"
    project = _shot_project(session, shot)
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="shot.identity_reviewed",
        payload={
            "shot_id": shot.id,
            "take_id": candidate.id,
            "take_version": candidate.version,
            "identity_status": candidate.identity_status,
            "actor": actor,
        },
    )
    session.commit()
    session.refresh(shot)
    return shot


def _candidate_take_or_conflict(session: Session, shot: Shot) -> Take:
    if shot.candidate_take is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "CANDIDATE_TAKE_MISSING", "message": "当前没有待复核的新版本"},
        )
    candidate = session.scalar(
        select(Take).where(
            Take.shot_id == shot.id,
            Take.kind == "STILL",
            Take.version == shot.candidate_take,
            Take.status == "READY",
        )
    )
    if candidate is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "CANDIDATE_TAKE_NOT_READY", "message": "新版本还在生成，请稍后再复核"},
        )
    return candidate


def _apply_candidate_state(session: Session, shot: Shot, candidate: Take) -> None:
    previous = session.scalar(select(Take).where(Take.shot_id == shot.id, Take.is_current))
    if previous is not None:
        previous.is_current = False
        previous.approval = "SUPERSEDED"
    candidate.is_current = True
    candidate.approval = "APPROVED"
    shot.current_take = candidate.version
    shot.current_take_id = candidate.id
    shot.candidate_take = None
    shot.status = "APPROVED"
    shot.lock_version += 1
    project = _shot_project(session, shot)
    project.timeline_version += 1
    project.status = "PREVIEW_READY"
    project.preview_approved = False
    project.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="shot.take_applied",
        payload={"shot_id": shot.id, "take_id": candidate.id, "take_version": candidate.version},
    )


def _latest_generation_options(
    session: Session, shot_id: str, take_version: int
) -> tuple[str | None, str, str | None]:
    jobs = session.scalars(
        select(Job)
        .where(Job.job_type == IMAGE_JOB_TYPE, Job.entity_id == shot_id)
        .order_by(Job.created_at.desc())
    ).all()
    for job in jobs:
        try:
            payload = json.loads(job.input_json or "{}")
        except json.JSONDecodeError:
            continue
        if int(payload.get("take_version", -1)) == take_version:
            return (
                payload.get("model") if isinstance(payload.get("model"), str) else None,
                payload.get("resolution") if isinstance(payload.get("resolution"), str) else "2K",
                payload.get("aspect_ratio")
                if isinstance(payload.get("aspect_ratio"), str)
                else None,
            )
    return None, "2K", None


def review_candidate_identity(
    session: Session,
    *,
    shot_id: str,
    decision: str,
    issues: list[str],
    note: str | None,
    expected_version: int,
    actor: str,
    request_idempotency_key: str,
    trace_id: str,
) -> tuple[Shot, JobRead | None]:
    if decision == "REGENERATE" and not issues:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "IDENTITY_ISSUES_REQUIRED",
                "message": "请至少选择一项需要调整的角色特征",
            },
        )
    if decision == "OVERRIDE_AND_APPLY" and not (note or "").strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "IDENTITY_OVERRIDE_REASON_REQUIRED",
                "message": "仍然应用这个版本前，请先说明原因",
            },
        )
    shot = shot_or_404(session, shot_id)
    if shot.lock_version != expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "VERSION_CONFLICT",
                "message": "这个镜头刚刚发生了变化，请刷新后再确认",
                "details": {"expected": expected_version, "actual": shot.lock_version},
            },
        )
    candidate = _candidate_take_or_conflict(session, shot)
    clean_note = note.strip() if note and note.strip() else None
    now = datetime.now(UTC)
    candidate.identity_review_decision = decision
    candidate.identity_review_issues_json = json.dumps(list(dict.fromkeys(issues)))
    candidate.identity_review_note = clean_note
    candidate.identity_review_actor = actor
    candidate.identity_reviewed_at = now
    candidate.identity_review_look_version = shot.character_look_version
    project = _shot_project(session, shot)
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="shot.identity_reviewed",
        payload={
            "shot_id": shot.id,
            "take_id": candidate.id,
            "take_version": candidate.version,
            "decision": decision,
            "issues": issues,
            "note": clean_note,
            "actor": actor,
            "identity_score": candidate.identity_score,
            "reference_asset_ids": json.loads(candidate.identity_reference_asset_ids_json or "[]"),
            "look_version": shot.character_look_version,
        },
    )

    if decision in {"APPROVE_AND_APPLY", "OVERRIDE_AND_APPLY"}:
        candidate.identity_status = "PASSED"
        candidate.identity_message = (
            "已确认与参考角色一致"
            if decision == "APPROVE_AND_APPLY"
            else f"已由 {actor} 说明原因后应用"
        )
        _apply_candidate_state(session, shot, candidate)
        session.commit()
        session.refresh(shot)
        return shot, None

    candidate.approval = "REJECTED"
    candidate.identity_message = "已标记角色差异，并据此生成新的候选版本"
    issue_guidance = "；".join(IDENTITY_ISSUE_PROMPTS[item] for item in issues)
    prompt = (
        f"{build_shot_prompt(project, shot)}\n"
        f"本次重做必须优先修正以下角色差异：{issue_guidance}。"
        "保持镜头构图、剧情动作、场景和光线意图不变。"
    )
    model, resolution, aspect_ratio = _latest_generation_options(
        session, shot.id, candidate.version
    )
    job, _replayed = create_shot_image_job(
        session,
        shot_id=shot.id,
        prompt=prompt,
        model=model,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        request_idempotency_key=f"identity-review:{request_idempotency_key}",
        trace_id=trace_id,
    )
    return shot, job


def apply_candidate_take(session: Session, shot_id: str) -> Shot:
    shot = shot_or_404(session, shot_id)
    candidate = _candidate_take_or_conflict(session, shot)
    if candidate.identity_status not in {"PASSED", "NOT_APPLICABLE"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDENTITY_REVIEW_REQUIRED",
                "message": "候选版本尚未通过角色身份一致性审核",
                "user_action": "重新生成，或在对比图片后人工确认角色身份",
                "retryable": False,
                "details": {
                    "identity_status": candidate.identity_status,
                    "identity_score": candidate.identity_score,
                    "identity_message": candidate.identity_message,
                },
            },
        )
    _apply_candidate_state(session, shot, candidate)
    session.commit()
    session.refresh(shot)
    return shot
