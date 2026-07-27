import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ARK_IMAGE_ASPECT_RATIOS, Settings, get_settings
from app.db.models import (
    Asset,
    Character,
    CharacterCandidate,
    CharacterLookVersion,
    GenerationRecord,
    Job,
    LocationVersion,
    Project,
    PropVersion,
    ScriptScene,
    ScriptVersion,
    StoryBibleVersion,
    VisualBibleVersion,
    VoiceProfile,
)
from app.schemas import CharacterRead, JobRead
from app.services.assets import register_file, resolve_asset_path
from app.services.character_image_qc import CharacterImageQualityReport
from app.services.events import append_event
from app.services.generation_records import ensure_generation_record
from app.services.image_provider import GeneratedImage
from app.services.jobs import enqueue_job, job_to_read
from app.services.production import list_characters
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.storyboards_v2 import _character_reference_asset_ids
from app.services.workspace import project_or_404


def _approved_script(session: Session, project_id: str) -> ScriptVersion | None:
    return session.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id, ScriptVersion.status == "APPROVED")
        .order_by(ScriptVersion.version.desc())
    )


def is_script_preproduction(session: Session, project_id: str) -> bool:
    return _approved_script(session, project_id) is not None


def _voice_description(character_payload: dict[str, object]) -> str:
    personality = [
        str(item).strip()
        for item in character_payload.get("personality", [])
        if str(item).strip()
    ]
    age = str(character_payload.get("age", "")).strip()
    dramatic_function = str(character_payload.get("dramatic_function", "")).strip()
    parts = [
        f"{age}感声线" if age else "",
        f"整体呈现{'、'.join(personality[:3])}" if personality else "",
        f"表演重点：{dramatic_function}" if dramatic_function else "",
    ]
    return "；".join(item for item in parts if item)


def prepare_preproduction(session: Session, job: Job) -> list[str]:
    script = _approved_script(session, job.project_id)
    if script is None:
        raise ValueError("已批准剧本不存在")
    bible = session.scalar(
        select(StoryBibleVersion)
        .where(StoryBibleVersion.project_id == job.project_id)
        .order_by(StoryBibleVersion.version.desc())
    )
    if bible is None:
        raise ValueError("故事设定集不存在")
    bible_payload = json.loads(bible.payload_json)
    characters = bible_payload.get("characters", [])
    if not isinstance(characters, list) or len(characters) < 2:
        raise ValueError("故事设定集至少需要两个角色")
    now = datetime.now(UTC)
    child_ids: list[str] = []
    for character_payload in characters:
        if not isinstance(character_payload, dict):
            continue
        character_key = str(character_payload["key"])
        character = session.scalar(
            select(Character).where(
                Character.project_id == job.project_id,
                Character.character_key == character_key,
            )
        )
        if character is None:
            character = Character(
                id=str(uuid4()),
                project_id=job.project_id,
                character_key=character_key,
                name=str(character_payload["name"]),
                role=str(character_payload["role"]),
                visual_brief=str(character_payload["visual_notes"]),
                status="NOT_GENERATED",
                locked_candidate_id=None,
                source_story_bible_version_id=bible.id,
                source_relationship_graph_id=None,
                current_profile_version_id=None,
                locked_identity_version_id=None,
                active_look_version_id=None,
                active_story_state_version_id=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(character)
            session.flush()
        voice = session.scalar(
            select(VoiceProfile).where(VoiceProfile.character_id == character.id)
        )
        if voice is None:
            voice_payload = {
                "gender_expression": "neutral",
                "age_impression": "adult",
                "tone": "natural-cinematic",
                "language": script.canonical_language,
                "voice_description": _voice_description(character_payload),
            }
            session.add(
                VoiceProfile(
                    id=str(uuid4()),
                    project_id=job.project_id,
                    character_id=character.id,
                    version=1,
                    provider="mock",
                    voice_key=f"mock-{character.character_key}",
                    payload_json=canonical_json(voice_payload),
                    pronunciation_json="{}",
                    consent_status="SYNTHETIC_ALLOWED",
                    cloning_enabled=False,
                    sample_asset_id=None,
                    content_hash=content_hash(voice_payload),
                    status="READY_FOR_REVIEW",
                    approved_at=None,
                    approved_by=None,
                    created_at=now,
                )
            )

    scene_locations = list(
        session.scalars(
            select(ScriptScene.location)
            .join(ScriptVersion, ScriptScene.script_version_id == ScriptVersion.id)
            .where(ScriptVersion.id == script.id)
            .distinct()
        ).all()
    )
    for index, location_name in enumerate(scene_locations, start=1):
        location_key = f"location-{index}"
        existing_location = session.scalar(
            select(LocationVersion).where(
                LocationVersion.project_id == job.project_id,
                LocationVersion.location_key == location_key,
            )
        )
        if existing_location is None:
            location_payload = {
                "name": location_name,
                "architecture": "contemporary urban",
                "lighting": "motivated practical lighting",
                "continuity": ["入口方向固定", "主光方向固定", "关键陈设位置固定"],
            }
            session.add(
                LocationVersion(
                    id=str(uuid4()),
                    project_id=job.project_id,
                    location_key=location_key,
                    version=1,
                    name=location_name,
                    payload_json=canonical_json(location_payload),
                    reference_asset_ids_json="[]",
                    content_hash=content_hash(location_payload),
                    status="READY_FOR_REVIEW",
                    approved_at=None,
                    approved_by=None,
                    created_at=now,
                )
            )
    existing_prop = session.scalar(
        select(PropVersion).where(
            PropVersion.project_id == job.project_id,
            PropVersion.prop_key == "story-evidence",
        )
    )
    if existing_prop is None:
        prop_payload = {
            "name": "旧照片",
            "dramatic_function": "触发冲突并承载反转线索",
            "continuity": ["裁切边缘一致", "污渍位置一致", "不得无故离开主角视线"],
        }
        session.add(
            PropVersion(
                id=str(uuid4()),
                project_id=job.project_id,
                prop_key="story-evidence",
                version=1,
                name="旧照片",
                payload_json=canonical_json(prop_payload),
                reference_asset_ids_json="[]",
                content_hash=content_hash(prop_payload),
                status="READY_FOR_REVIEW",
                approved_at=None,
                approved_by=None,
                created_at=now,
            )
        )
    project = project_or_404(session, job.project_id)
    project_characters = list(
        session.scalars(
            select(Character).where(
                Character.project_id == job.project_id,
                Character.status != "SUPERSEDED",
            )
        ).all()
    )
    if project_characters and all(item.locked_identity_version_id for item in project_characters):
        project.status = "PREPRODUCTION_READY"
        project.lock_version += 1
        project.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="preproduction.extracted",
        payload={
            "character_count": len(characters),
            "candidate_job_ids": child_ids,
            "locked_identity_ids": [item.locked_identity_version_id for item in project_characters],
        },
    )
    session.flush()
    return child_ids


def materialize_character_candidate(
    session: Session,
    settings: Settings,
    job: Job,
    image: GeneratedImage,
    *,
    quality_report: CharacterImageQualityReport | None = None,
) -> tuple[Asset, CharacterCandidate]:
    payload = json.loads(job.input_json)
    character = session.get(Character, str(payload["character_id"]))
    if character is None:
        raise ValueError("角色不存在")
    ordinal = int(payload["ordinal"])

    def trace_generation(
        asset: Asset,
        candidate: CharacterCandidate,
        *,
        quality_status: str,
        reused: bool,
    ) -> None:
        ensure_generation_record(
            session,
            job=job,
            capability="PREPRODUCTION_CHARACTER_CANDIDATE",
            provider=asset.provider,
            model=image.model,
            config_version="preproduction-character-v1",
            prompt=str(payload.get("prompt", "")),
            seed=payload.get("seed"),
            reference_asset_ids=[],
            provider_request_id=image.request_id,
            provider_task_id=None,
            output_asset_id=asset.id,
            entity_type="character_candidate",
            entity_id=candidate.id,
            estimated_cost_usd=0.0 if asset.provider == "mock" else None,
            metadata={
                "character_id": character.id,
                "ordinal": ordinal,
                "quality_status": quality_status,
                "reused_existing_output": reused,
            },
        )

    existing = session.scalar(
        select(CharacterCandidate).where(
            CharacterCandidate.character_id == character.id,
            CharacterCandidate.ordinal == ordinal,
        )
    )
    if existing is not None:
        asset = session.get(Asset, existing.asset_id)
        if asset is None:
            raise ValueError("角色候选资产不存在")
        trace_generation(
            asset,
            existing,
            quality_status=existing.status,
            reused=True,
        )
        return asset, existing
    tmp_dir = settings.data_dir / "tmp" / job.id / "character-candidate"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if image.mime == "image/png" else ".jpg"
    image_path = Path(tmp_dir / f"candidate-{ordinal}{suffix}")
    image_path.write_bytes(image.content)
    candidate_id = str(uuid4())
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind="character_candidate",
        source=image_path,
        source_entity_type="character_candidate",
        source_entity_id=candidate_id,
        mime=image.mime,
        width=image.width,
        height=image.height,
    )
    asset.provider = "volcengine-ark" if settings.ark_api_key else "mock"
    metadata: dict[str, object] = {
        "model": image.model,
        "provider_request_id": image.request_id,
        "source_url": image.source_url,
        "seed": payload["seed"],
    }
    if quality_report is not None:
        metadata["generation_quality"] = quality_report.as_dict()
    asset.metadata_json = canonical_json(metadata)
    now = datetime.now(UTC)
    quality_status = quality_report.status if quality_report is not None else "PASSED"
    candidate = CharacterCandidate(
        id=candidate_id,
        project_id=job.project_id,
        character_id=character.id,
        ordinal=ordinal,
        asset_id=asset.id,
        seed=str(payload["seed"]),
        status=("READY" if quality_status != "FAILED" else "QC_FAILED"),
        review_status=("PENDING_SELECTION" if quality_status == "PASSED" else "QC_REVIEW_REQUIRED"),
        selected=False,
        created_at=now,
    )
    session.add(candidate)
    trace_generation(
        asset,
        candidate,
        quality_status=quality_status,
        reused=False,
    )
    session.flush()
    materialized_count = session.scalar(
        select(func.count(CharacterCandidate.id)).where(
            CharacterCandidate.character_id == character.id
        )
    )
    if materialized_count >= int(payload["candidate_count"]):
        candidate_statuses = list(
            session.scalars(
                select(CharacterCandidate.status).where(
                    CharacterCandidate.character_id == character.id
                )
            ).all()
        )
        character.status = (
            "CANDIDATES_READY"
            if candidate_statuses and all(status == "READY" for status in candidate_statuses)
            else "REVIEW_REQUIRED"
        )
        character.updated_at = now
    all_characters = list(
        session.scalars(select(Character).where(Character.project_id == job.project_id)).all()
    )
    if all(item.status == "CANDIDATES_READY" for item in all_characters):
        project = project_or_404(session, job.project_id)
        project.status = "PREPRODUCTION_READY"
        project.lock_version += 1
        project.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="character.candidate_ready",
        payload={
            "character_id": character.id,
            "candidate_id": candidate.id,
            "ordinal": ordinal,
        },
    )
    session.flush()
    return asset, candidate


def _world_asset_record(
    session: Session,
    *,
    project_id: str,
    asset_type: str,
    version_id: str,
) -> LocationVersion | PropVersion:
    model = (
        LocationVersion
        if asset_type == "location"
        else PropVersion
        if asset_type == "prop"
        else None
    )
    if model is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "不支持的世界资产类型"},
        )
    record = session.get(model, version_id)
    if record is None or record.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "场景或道具版本不存在"},
        )
    return record


def _resolve_world_asset_characters(
    session: Session,
    *,
    project_id: str,
    character_ids: list[str],
) -> tuple[list[Character], list[str]]:
    """校验并解析关联角色的锁定形象参考图。"""
    if not character_ids:
        return [], []
    characters: list[Character] = []
    reference_asset_ids: list[str] = []
    for character_id in character_ids:
        character = session.get(Character, character_id)
        if character is None or character.project_id != project_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "CHARACTER_REFERENCE_NOT_FOUND",
                    "message": "关联角色不存在或不属于当前项目",
                },
            )
        asset_ids = _character_reference_asset_ids(session, character)
        if not asset_ids:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "CHARACTER_REFERENCE_NOT_FOUND",
                    "message": f"角色「{character.name}」尚未锁定可用形象参考图",
                },
            )
        characters.append(character)
        for asset_id in asset_ids:
            if asset_id not in reference_asset_ids:
                reference_asset_ids.append(asset_id)
    return characters, reference_asset_ids[:6]


def _world_asset_character_lock_prompt(characters: list[Character]) -> str:
    if not characters:
        return ""
    lines = [
        (
            f"- 参考图对应角色：{character.name}（{character.role}）；"
            f"{(character.visual_brief or '').strip() or '沿用锁定身份五官与发型'}"
        )
        for character in characters
    ]
    return "\n".join(
        (
            "角色形象锁定（硬约束）：",
            *lines,
            "- 输入参考图是关联角色的外貌基准；画面中呈现的关联人物必须与参考图为同一人。",
            "- 允许改变表情、姿势、景别、光线与背景；禁止换脸、混脸或另造相似替身。",
            "- 禁止无关路人抢戏；未关联角色不要入镜。",
        )
    )


def _validate_world_asset_source_asset(
    session: Session,
    *,
    project_id: str,
    asset_type: str,
    record: LocationVersion | PropVersion,
    source_asset_id: str | None,
) -> Asset | None:
    if not source_asset_id:
        return None
    source_asset = session.get(Asset, source_asset_id)
    if (
        source_asset is None
        or source_asset.project_id != project_id
        or source_asset.kind != "world_asset_reference"
        or source_asset.source_entity_type != f"{asset_type}_version"
        or source_asset.source_entity_id != record.id
        or source_asset.status != "READY"
    ):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "WORLD_ASSET_REFERENCE_NOT_FOUND",
                "message": "用于修改生成的参考图不存在或不属于当前资产",
            },
        )
    return source_asset


def build_world_asset_image_prompts(
    *,
    asset_type: str,
    record: LocationVersion | PropVersion,
    count: int = 1,
    characters: list[Character] | None = None,
    source_asset: Asset | None = None,
    adjustment_prompt: str | None = None,
    custom_base_prompt: str | None = None,
) -> dict[str, object]:
    """组装世界资产参考图基础提示词与风格变体（preview / generate 共用）。"""
    payload = json.loads(record.payload_json)
    linked_characters = characters or []
    character_lock = _world_asset_character_lock_prompt(linked_characters)
    if asset_type == "location":
        if linked_characters:
            default_base = (
                f"影视短剧场景设定基准图：{record.name}。"
                f"空间与视觉设定：{canonical_json(payload)}。"
                "建立清晰、可重复使用的空间结构、入口方向、主光方向与关键陈设位置。"
                "关联角色可作为空间尺度或出镜参考，不强制必须入镜；"
                "无文字、无标志、无水印，写实电影美术概念图，横向构图。"
            )
        else:
            default_base = (
                f"影视短剧场景设定基准图：{record.name}。"
                f"空间与视觉设定：{canonical_json(payload)}。"
                "建立清晰、可重复使用的空间结构、入口方向、主光方向与关键陈设位置。"
                "无人、无文字、无标志、无水印，写实电影美术概念图，横向构图。"
            )
        label = f"{record.name} · 场景参考图"
        style_variants = (
            ("cinematic-realism", "写实电影美术", "真实材质与自然光影，接近实景置景勘景照片"),
            ("concept-art", "电影概念设计", "强化美术设计与空间层次，使用精细电影概念设计表达"),
            ("atmospheric", "氛围叙事", "强化环境氛围、色彩关系与戏剧性光影，但保持空间结构不变"),
        )
    else:
        if linked_characters:
            default_base = (
                f"影视短剧关键道具设定基准图：{record.name}。"
                f"外观与叙事设定：{canonical_json(payload)}。"
                "完整展示道具轮廓、材质、颜色、磨损和关键细节，便于跨镜头保持一致。"
                "允许道具上呈现关联角色的外貌（如照片、画像中的人物），禁止无关路人；"
                "无文字说明、无标志、无水印，中性背景，写实电影道具设定图。"
            )
        else:
            default_base = (
                f"影视短剧关键道具设定基准图：{record.name}。"
                f"外观与叙事设定：{canonical_json(payload)}。"
                "完整展示道具轮廓、材质、颜色、磨损和关键细节，便于跨镜头保持一致。"
                "单一道具、无人物、无文字说明、无标志、无水印，中性背景，写实电影道具设定图。"
            )
        label = f"{record.name} · 道具参考图"
        style_variants = (
            ("studio-realism", "写实棚拍", "真实材质、准确比例与柔和棚拍光线"),
            (
                "production-design",
                "电影道具设计",
                "强化结构、工艺和可制作细节，使用电影道具概念设计表达",
            ),
            ("narrative-wear", "叙事质感", "强化与剧情相符的使用痕迹、年代感和戏剧性光影"),
        )
    if character_lock:
        default_base = f"{default_base}\n{character_lock}"
    base_prompt = (
        custom_base_prompt.strip()
        if custom_base_prompt and custom_base_prompt.strip()
        else default_base
    )
    if source_asset is not None and adjustment_prompt is not None:
        style_variants = (
            (
                "reference-refinement",
                "修改生成",
                (
                    f"以输入参考图为直接视觉基础，只执行以下修改要求："
                    f"{adjustment_prompt.strip()}。"
                    "除明确要求修改的内容外，保持原图的主体身份、空间结构、构图关系、"
                    "关键陈设或道具细节不变。"
                ),
            ),
        )
    variants: list[dict[str, object]] = []
    for style_id, style_label, style_prompt in style_variants[:count]:
        variants.append(
            {
                "style_id": style_id,
                "style_label": style_label,
                "prompt": (
                    f"{base_prompt}"
                    f"本候选采用「{style_label}」风格：{style_prompt}。"
                    "必须严格保持上述同一主题、叙事设定和关键结构，不得因风格变化改写场景或道具身份。"
                ),
            }
        )
    return {
        "base_prompt": base_prompt,
        "default_base_prompt": default_base,
        "label": label,
        "variants": variants,
    }


def preview_world_asset_image_prompts(
    session: Session,
    *,
    project_id: str,
    asset_type: str,
    version_id: str,
    expected_version: int,
    count: int = 1,
    character_ids: list[str] | None = None,
    source_asset_id: str | None = None,
    adjustment_prompt: str | None = None,
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    record = _world_asset_record(
        session,
        project_id=project_id,
        asset_type=asset_type,
        version_id=version_id,
    )
    characters, _ = _resolve_world_asset_characters(
        session,
        project_id=project_id,
        character_ids=character_ids or [],
    )
    source_asset = _validate_world_asset_source_asset(
        session,
        project_id=project_id,
        asset_type=asset_type,
        record=record,
        source_asset_id=source_asset_id,
    )
    built = build_world_asset_image_prompts(
        asset_type=asset_type,
        record=record,
        count=count,
        characters=characters,
        source_asset=source_asset,
        adjustment_prompt=adjustment_prompt,
    )
    return {
        "base_prompt": built["base_prompt"],
        "variants": built["variants"],
    }


def request_world_asset_image_generation(
    session: Session,
    *,
    project_id: str,
    asset_type: str,
    version_id: str,
    expected_version: int,
    count: int = 1,
    character_ids: list[str] | None = None,
    custom_base_prompt: str | None = None,
    aspect_ratio: str = "16:9",
    source_asset_id: str | None = None,
    adjustment_prompt: str | None = None,
    actor: str,
    idempotency_key: str,
    trace_id: str,
    commit: bool = True,
) -> tuple[list[JobRead], bool]:
    project = project_or_404(session, project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "PREPRODUCTION_READY":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PREPRODUCTION_NOT_READY",
                "message": "当前阶段不能生成场景或道具参考图",
            },
        )
    if aspect_ratio not in ARK_IMAGE_ASPECT_RATIOS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNSUPPORTED_IMAGE_ASPECT_RATIO",
                "message": "所选画面比例不受支持",
                "details": {"aspect_ratio": aspect_ratio},
            },
        )
    record = _world_asset_record(
        session,
        project_id=project_id,
        asset_type=asset_type,
        version_id=version_id,
    )
    characters, character_reference_asset_ids = _resolve_world_asset_characters(
        session,
        project_id=project_id,
        character_ids=character_ids or [],
    )
    source_asset = _validate_world_asset_source_asset(
        session,
        project_id=project_id,
        asset_type=asset_type,
        record=record,
        source_asset_id=source_asset_id,
    )
    built = build_world_asset_image_prompts(
        asset_type=asset_type,
        record=record,
        count=count,
        characters=characters,
        source_asset=source_asset,
        adjustment_prompt=adjustment_prompt,
        custom_base_prompt=custom_base_prompt,
    )
    resolved_character_ids = [character.id for character in characters]
    batch_id = str(uuid4())
    jobs: list[Job] = []
    replayed_flags: list[bool] = []
    for style_slot, variant in enumerate(built["variants"], start=1):
        style_id = str(variant["style_id"])
        style_label = str(variant["style_label"])
        prompt = (
            f"{str(variant['prompt']).rstrip('。')}。"
            f"严格使用 {aspect_ratio} 画面比例。"
        )
        job, replayed = enqueue_job(
            session,
            project_id=project_id,
            job_type="GENERATE_WORLD_ASSET_IMAGE",
            entity_type=f"{asset_type}_version",
            entity_id=record.id,
            idempotency_key=(
                f"{project_id}:GENERATE_WORLD_ASSET_IMAGE:{asset_type}:"
                f"{record.id}:{idempotency_key}:{style_slot}"
            ),
            input_payload={
                "asset_type": asset_type,
                "version_id": record.id,
                "prompt": prompt,
                "actor": actor,
                "batch_id": batch_id,
                "style_slot": style_slot,
                "style_id": style_id,
                "style_label": style_label,
                "aspect_ratio": aspect_ratio,
                "source_asset_id": source_asset.id if source_asset is not None else None,
                "adjustment_prompt": adjustment_prompt.strip() if adjustment_prompt else None,
                "character_ids": resolved_character_ids,
                "character_reference_asset_ids": character_reference_asset_ids,
                "custom_base_prompt": (
                    custom_base_prompt.strip() if custom_base_prompt else None
                ),
            },
            label=f"{built['label']} · {style_label}",
            stage=f"等待生成参考图 {style_slot}/{count}",
            trace_id=trace_id,
            estimated_seconds=20,
            retryable=True,
        )
        jobs.append(job)
        replayed_flags.append(replayed)
    if commit:
        session.commit()
    for job in jobs:
        session.refresh(job)
    return [job_to_read(job) for job in jobs], all(replayed_flags)


def materialize_world_asset_image(
    session: Session,
    settings: Settings,
    job: Job,
    image: GeneratedImage,
) -> Asset:
    payload = json.loads(job.input_json)
    asset_type = str(payload["asset_type"])
    record = _world_asset_record(
        session,
        project_id=job.project_id,
        asset_type=asset_type,
        version_id=str(payload["version_id"]),
    )
    tmp_dir = settings.data_dir / "tmp" / job.id / "world-asset"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if image.mime == "image/png" else ".jpg"
    image_path = Path(tmp_dir / f"{asset_type}-reference{suffix}")
    image_path.write_bytes(image.content)
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind="world_asset_reference",
        source=image_path,
        source_entity_type=f"{asset_type}_version",
        source_entity_id=record.id,
        mime=image.mime,
        width=image.width,
        height=image.height,
    )
    asset.provider = "volcengine-ark" if settings.ark_api_key else "mock"
    character_ids = [
        item for item in payload.get("character_ids", []) if isinstance(item, str)
    ]
    character_reference_asset_ids = [
        item
        for item in payload.get("character_reference_asset_ids", [])
        if isinstance(item, str)
    ]
    asset.metadata_json = canonical_json(
        {
            "model": image.model,
            "provider_request_id": image.request_id,
            "source_url": image.source_url,
            "asset_type": asset_type,
            "version_id": record.id,
            "batch_id": payload.get("batch_id"),
            "style_slot": payload.get("style_slot"),
            "style_id": payload.get("style_id"),
            "style_label": payload.get("style_label"),
            "source_asset_id": payload.get("source_asset_id"),
            "adjustment_prompt": payload.get("adjustment_prompt"),
            "character_ids": character_ids,
            "character_reference_asset_ids": character_reference_asset_ids,
            "custom_base_prompt": payload.get("custom_base_prompt"),
            "prompt": payload.get("prompt"),
            "aspect_ratio": payload.get("aspect_ratio") or "16:9",
        }
    )
    ensure_generation_record(
        session,
        job=job,
        capability="PREPRODUCTION_WORLD_ASSET_REFERENCE",
        provider=asset.provider,
        model=image.model,
        config_version="world-asset-reference-v1",
        prompt=str(payload["prompt"]),
        seed=None,
        reference_asset_ids=character_reference_asset_ids,
        provider_request_id=image.request_id,
        provider_task_id=None,
        output_asset_id=asset.id,
        entity_type=f"{asset_type}_version",
        entity_id=record.id,
        estimated_cost_usd=0.0 if asset.provider == "mock" else None,
        metadata={
            "asset_type": asset_type,
            "batch_id": payload.get("batch_id"),
            "style_id": payload.get("style_id"),
            "style_label": payload.get("style_label"),
            "source_asset_id": payload.get("source_asset_id"),
            "adjustment_prompt": payload.get("adjustment_prompt"),
            "character_ids": character_ids,
            "character_reference_asset_ids": character_reference_asset_ids,
            "custom_base_prompt": payload.get("custom_base_prompt"),
        },
    )
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="world_asset.reference_generated",
        payload={
            "asset_type": asset_type,
            "version_id": record.id,
            "asset_id": asset.id,
            "batch_id": payload.get("batch_id"),
            "style_id": payload.get("style_id"),
            "source_asset_id": payload.get("source_asset_id"),
        },
    )
    session.flush()
    return asset


def lock_world_asset_reference(
    session: Session,
    *,
    project_id: str,
    asset_type: str,
    version_id: str,
    asset_id: str,
    expected_version: int,
    actor: str,
    commit: bool = True,
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "PREPRODUCTION_READY":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PREPRODUCTION_NOT_READY",
                "message": "当前阶段不能锁定场景或道具参考图",
            },
        )
    record = _world_asset_record(
        session,
        project_id=project_id,
        asset_type=asset_type,
        version_id=version_id,
    )
    asset = session.get(Asset, asset_id)
    if (
        asset is None
        or asset.project_id != project_id
        or asset.kind != "world_asset_reference"
        or asset.source_entity_type != f"{asset_type}_version"
        or asset.source_entity_id != record.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "参考图候选不存在或不属于当前资产"},
        )
    record.reference_asset_ids_json = canonical_json([asset.id])
    record.content_hash = content_hash(
        {
            "payload": json.loads(record.payload_json),
            "references": [asset.id],
        }
    )
    project.lock_version += 1
    project.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project_id,
        job_id=None,
        event_type="world_asset.reference_locked",
        payload={
            "asset_type": asset_type,
            "version_id": record.id,
            "asset_id": asset.id,
            "actor": actor,
        },
    )
    session.flush()
    if commit:
        session.commit()
    return {
        "asset_type": asset_type,
        "version_id": record.id,
        "asset_id": asset.id,
        "locked": True,
        "project_lock_version": project.lock_version,
    }


def delete_world_asset_reference(
    session: Session,
    *,
    project_id: str,
    asset_type: str,
    version_id: str,
    asset_id: str,
    expected_version: int,
    actor: str,
    settings: Settings | None = None,
    commit: bool = True,
) -> dict[str, object]:
    """删除场景/道具参考图候选；已锁定图不可删。"""
    project = project_or_404(session, project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    record = _world_asset_record(
        session,
        project_id=project_id,
        asset_type=asset_type,
        version_id=version_id,
    )
    asset = session.get(Asset, asset_id)
    if (
        asset is None
        or asset.project_id != project_id
        or asset.kind != "world_asset_reference"
        or asset.source_entity_type != f"{asset_type}_version"
        or asset.source_entity_id != record.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "参考图候选不存在或不属于当前资产"},
        )
    locked_ids = json.loads(record.reference_asset_ids_json or "[]")
    if asset.id in locked_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WORLD_ASSET_REFERENCE_LOCKED",
                "message": "已锁定的参考图不能删除",
                "user_action": "请先锁定其他候选图，再删除此图",
                "retryable": False,
            },
        )
    active_jobs = session.scalars(
        select(Job).where(
            Job.project_id == project_id,
            Job.status.in_({"PENDING", "RETRY_WAIT", "RUNNING", "CANCEL_REQUESTED"}),
        )
    ).all()
    for job in active_jobs:
        payload = json.loads(job.input_json or "{}")
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("source_asset_id") == asset.id
            or payload.get("asset_id") == asset.id
            or asset.id in (payload.get("reference_asset_ids") or [])
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WORLD_ASSET_GENERATION_ACTIVE",
                    "message": "该参考图正在被生成任务使用，暂时不能删除",
                    "user_action": "请等待相关生成任务结束后重试",
                    "retryable": True,
                },
            )
    runtime_settings = settings or get_settings()
    shared = session.scalar(
        select(Asset).where(Asset.storage_key == asset.storage_key, Asset.id != asset.id)
    )
    cleanup_path: Path | None = None
    if shared is None:
        try:
            cleanup_path = resolve_asset_path(runtime_settings, asset)
        except HTTPException:
            cleanup_path = None
    session.delete(asset)
    project.lock_version += 1
    project.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project_id,
        job_id=None,
        event_type="world_asset.reference_deleted",
        payload={
            "asset_type": asset_type,
            "version_id": record.id,
            "asset_id": asset_id,
            "actor": actor,
        },
    )
    session.flush()
    if commit:
        session.commit()
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)
    return {
        "asset_type": asset_type,
        "version_id": record.id,
        "asset_id": asset_id,
        "deleted": True,
        "project_lock_version": project.lock_version,
    }


def lock_character_for_preproduction(
    session: Session,
    *,
    project: Project,
    character: Character,
    candidate: CharacterCandidate,
    expected_version: int,
    actor: str,
    trace_id: str,
    commit: bool = True,
) -> tuple[CharacterRead, JobRead, bool]:
    business_key = (
        f"{project.id}:GENERATE_CHARACTER_LOOKS:{character.id}:candidate-{candidate.id}:looks-v1"
    )
    existing_job = session.scalar(select(Job).where(Job.idempotency_key == business_key))
    if existing_job is not None:
        current = next(
            item for item in list_characters(session, project.id) if item.id == character.id
        )
        return current, job_to_read(existing_job), True
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "PREPRODUCTION_READY" or candidate.status != "READY":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PREPRODUCTION_NOT_READY",
                "message": "当前角色候选不能锁定",
                "details": {
                    "project_status": project.status,
                    "candidate_status": candidate.status,
                },
            },
        )
    now = datetime.now(UTC)
    character.locked_candidate_id = candidate.id
    character.status = "LOCKED"
    character.lock_version += 1
    character.updated_at = now
    candidate.selected = True
    project.lock_version += 1
    project.updated_at = now
    job, replayed = enqueue_job(
        session,
        project_id=project.id,
        job_type="GENERATE_CHARACTER_LOOKS",
        entity_type="character",
        entity_id=character.id,
        idempotency_key=business_key,
        input_payload={
            "character_id": character.id,
            "candidate_id": candidate.id,
            "reference_asset_id": candidate.asset_id,
            "actor": actor,
        },
        label=f"{character.name} · 造型设定",
        stage="等待生成角色造型版本",
        trace_id=trace_id,
        estimated_seconds=2,
        retryable=True,
    )
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="character.reference_locked",
        payload={"character_id": character.id, "candidate_id": candidate.id},
    )
    session.flush()
    if commit:
        session.commit()
    session.refresh(job)
    current = next(item for item in list_characters(session, project.id) if item.id == character.id)
    return current, job_to_read(job), replayed


def materialize_character_looks(session: Session, job: Job) -> list[str]:
    payload = json.loads(job.input_json)
    character = session.get(Character, str(payload["character_id"]))
    if character is None:
        raise ValueError("角色不存在")
    existing = list(
        session.scalars(
            select(CharacterLookVersion)
            .where(CharacterLookVersion.character_id == character.id)
            .order_by(CharacterLookVersion.version)
        ).all()
    )
    if len(existing) >= 2:
        return [item.id for item in existing]
    now = datetime.now(UTC)
    reference_ids = [str(payload["reference_asset_id"])]
    looks = (
        (
            "造型 1 · 基础造型",
            {
                "wardrobe": "主线连续性基础服装",
                "hair": "保持锁定候选发型",
                "makeup": "自然电影妆",
                "accessories": [],
            },
        ),
        (
            "造型 2 · 情绪升级",
            {
                "wardrobe": "同一基础服装，增加雨夜湿润与磨损状态",
                "hair": "保持发型轮廓，加入受潮细节",
                "makeup": "疲惫感与轻微雨水痕迹",
                "accessories": [],
            },
        ),
    )
    created_ids: list[str] = []
    for version, (label, look_payload) in enumerate(looks, start=1):
        record = CharacterLookVersion(
            id=str(uuid4()),
            project_id=job.project_id,
            character_id=character.id,
            version=version,
            label=label,
            usage_scope="GLOBAL" if version == 1 else "EMOTIONAL_ESCALATION",
            payload_json=canonical_json(look_payload),
            reference_asset_ids_json=canonical_json(reference_ids),
            content_hash=content_hash({"payload": look_payload, "references": reference_ids}),
            status="READY_FOR_REVIEW",
            approved_at=None,
            approved_by=None,
            created_at=now,
        )
        session.add(record)
        created_ids.append(record.id)
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="character.looks_ready",
        payload={"character_id": character.id, "look_ids": created_ids},
    )
    session.flush()
    return created_ids


def preproduction_workspace(session: Session, project_id: str) -> dict[str, object]:
    project_or_404(session, project_id)
    characters = list_characters(session, project_id)
    character_keys_by_id = {item.id: item.character_key for item in characters}
    story_bible = session.scalar(
        select(StoryBibleVersion)
        .where(StoryBibleVersion.project_id == project_id)
        .order_by(StoryBibleVersion.version.desc())
    )
    bible_characters_by_key: dict[str, dict[str, object]] = {}
    if story_bible is not None:
        for payload in json.loads(story_bible.payload_json).get("characters", []):
            if isinstance(payload, dict) and isinstance(payload.get("key"), str):
                bible_characters_by_key[str(payload["key"])] = payload
    looks = session.scalars(
        select(CharacterLookVersion)
        .where(CharacterLookVersion.project_id == project_id)
        .order_by(CharacterLookVersion.character_id, CharacterLookVersion.version)
    ).all()
    locations = session.scalars(
        select(LocationVersion).where(LocationVersion.project_id == project_id)
    ).all()
    props = session.scalars(select(PropVersion).where(PropVersion.project_id == project_id)).all()
    world_reference_assets = session.scalars(
        select(Asset)
        .where(
            Asset.project_id == project_id,
            Asset.kind == "world_asset_reference",
        )
        .order_by(Asset.created_at.desc())
    ).all()
    generation_prompt_by_asset: dict[str, str] = {}
    asset_ids = [asset.id for asset in world_reference_assets]
    if asset_ids:
        for record in session.scalars(
            select(GenerationRecord).where(GenerationRecord.output_asset_id.in_(asset_ids))
        ).all():
            if not record.output_asset_id:
                continue
            try:
                snapshot = json.loads(record.input_snapshot_json or "{}")
            except json.JSONDecodeError:
                snapshot = {}
            prompt = snapshot.get("prompt") if isinstance(snapshot, dict) else None
            if isinstance(prompt, str) and prompt.strip():
                generation_prompt_by_asset[record.output_asset_id] = prompt.strip()
    reference_candidates_by_entity: dict[str, list[dict[str, object]]] = {}
    for asset in world_reference_assets:
        asset_metadata = json.loads(asset.metadata_json or "{}")
        metadata_prompt = asset_metadata.get("prompt")
        generation_prompt = (
            metadata_prompt.strip()
            if isinstance(metadata_prompt, str) and metadata_prompt.strip()
            else generation_prompt_by_asset.get(asset.id)
        )
        reference_candidates_by_entity.setdefault(asset.source_entity_id, []).append(
            {
                "id": asset.id,
                "asset_url": f"/api/v1/assets/{asset.id}/content",
                "status": asset.status,
                "created_at": asset.created_at,
                "batch_id": asset_metadata.get("batch_id"),
                "style_id": asset_metadata.get("style_id"),
                "style_label": asset_metadata.get("style_label"),
                "source_asset_id": asset_metadata.get("source_asset_id"),
                "adjustment_prompt": asset_metadata.get("adjustment_prompt"),
                "character_ids": asset_metadata.get("character_ids"),
                "aspect_ratio": asset_metadata.get("aspect_ratio") or "16:9",
                "generation_prompt": generation_prompt,
            }
        )
    voices = session.scalars(
        select(VoiceProfile).where(VoiceProfile.project_id == project_id)
    ).all()
    voice_payloads: dict[str, dict[str, object]] = {}
    for item in voices:
        payload = json.loads(item.payload_json)
        if not payload.get("voice_description"):
            character_payload = bible_characters_by_key.get(
                character_keys_by_id.get(item.character_id, "")
            )
            if character_payload is not None:
                payload["voice_description"] = _voice_description(character_payload)
        voice_payloads[item.id] = payload
    visual_bibles = session.scalars(
        select(VisualBibleVersion)
        .where(VisualBibleVersion.project_id == project_id)
        .order_by(VisualBibleVersion.version.desc())
    ).all()
    return {
        "characters": characters,
        "looks": [
            {
                "id": item.id,
                "character_id": item.character_id,
                "version": item.version,
                "label": item.label,
                "usage_scope": item.usage_scope,
                "payload": json.loads(item.payload_json),
                "reference_asset_ids": json.loads(item.reference_asset_ids_json),
                "status": item.status,
                "content_hash": item.content_hash,
            }
            for item in looks
        ],
        "locations": [
            {
                "id": item.id,
                "key": item.location_key,
                "version": item.version,
                "name": item.name,
                "payload": json.loads(item.payload_json),
                "reference_asset_ids": json.loads(item.reference_asset_ids_json),
                "image_candidates": reference_candidates_by_entity.get(item.id, []),
                "status": item.status,
                "content_hash": item.content_hash,
            }
            for item in locations
        ],
        "props": [
            {
                "id": item.id,
                "key": item.prop_key,
                "version": item.version,
                "name": item.name,
                "payload": json.loads(item.payload_json),
                "reference_asset_ids": json.loads(item.reference_asset_ids_json),
                "image_candidates": reference_candidates_by_entity.get(item.id, []),
                "status": item.status,
                "content_hash": item.content_hash,
            }
            for item in props
        ],
        "voices": [
            {
                "id": item.id,
                "character_id": item.character_id,
                "version": item.version,
                "provider": item.provider,
                "voice_key": item.voice_key,
                "payload": voice_payloads[item.id],
                "pronunciation": json.loads(item.pronunciation_json),
                "consent_status": item.consent_status,
                "cloning_enabled": item.cloning_enabled,
                "status": item.status,
            }
            for item in voices
        ],
        "visual_bibles": [
            {
                "id": item.id,
                "version": item.version,
                "status": item.status,
                "character_look_ids": json.loads(item.character_look_ids_json),
                "location_version_ids": json.loads(item.location_version_ids_json),
                "prop_version_ids": json.loads(item.prop_version_ids_json),
                "voice_profile_ids": json.loads(item.voice_profile_ids_json),
                "content_hash": item.content_hash,
            }
            for item in visual_bibles
        ],
    }


def approve_preproduction(
    session: Session,
    *,
    project_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
    commit: bool = True,
) -> tuple[dict[str, object], JobRead, bool]:
    project = project_or_404(session, project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "PREPRODUCTION_READY":
        raise HTTPException(
            status_code=409,
            detail={"code": "PREPRODUCTION_NOT_READY", "message": "前期制作尚未达到批准条件"},
        )
    characters = list(
        session.scalars(
            select(Character).where(
                Character.project_id == project_id,
                Character.status != "SUPERSEDED",
            )
        ).all()
    )
    looks = list(
        session.scalars(
            select(CharacterLookVersion).where(CharacterLookVersion.project_id == project_id)
        ).all()
    )
    locations = list(
        session.scalars(
            select(LocationVersion).where(LocationVersion.project_id == project_id)
        ).all()
    )
    props = list(
        session.scalars(select(PropVersion).where(PropVersion.project_id == project_id)).all()
    )
    voices = list(
        session.scalars(select(VoiceProfile).where(VoiceProfile.project_id == project_id)).all()
    )
    if not characters or any(item.locked_candidate_id is None for item in characters):
        raise HTTPException(
            status_code=409,
            detail={"code": "CHARACTER_LOCK_REQUIRED", "message": "所有角色都必须锁定候选"},
        )
    if any(sum(1 for look in looks if look.character_id == item.id) < 1 for item in characters):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LOOKS_REQUIRED",
                "message": "每个角色至少需要一个已锁定身份的基础造型版本",
            },
        )
    missing_world_references = [
        item.name
        for item in [*locations, *props]
        if not json.loads(item.reference_asset_ids_json or "[]")
    ]
    if missing_world_references:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WORLD_ASSET_REFERENCES_REQUIRED",
                "message": "核心场景和关键道具必须生成并锁定参考图",
                "details": {"missing": missing_world_references},
            },
        )
    now = datetime.now(UTC)
    for record in [*looks, *locations, *props, *voices]:
        record.status = "APPROVED"
        record.approved_at = now
        record.approved_by = actor
    bundle_payload = {
        "characters": [item.id for item in characters],
        "looks": [item.id for item in looks],
        "locations": [item.id for item in locations],
        "props": [item.id for item in props],
        "voices": [item.id for item in voices],
    }
    version = (
        session.scalar(
            select(func.max(VisualBibleVersion.version)).where(
                VisualBibleVersion.project_id == project_id
            )
        )
        or 0
    ) + 1
    visual_bible = VisualBibleVersion(
        id=str(uuid4()),
        project_id=project_id,
        version=version,
        status="APPROVED",
        character_look_ids_json=canonical_json(bundle_payload["looks"]),
        location_version_ids_json=canonical_json(bundle_payload["locations"]),
        prop_version_ids_json=canonical_json(bundle_payload["props"]),
        voice_profile_ids_json=canonical_json(bundle_payload["voices"]),
        payload_json=canonical_json(bundle_payload),
        content_hash=content_hash(bundle_payload),
        approved_at=now,
        approved_by=actor,
        created_at=now,
    )
    session.add(visual_bible)
    project.status = "PREPRODUCTION_APPROVED"
    project.lock_version += 1
    project.updated_at = now
    job, replayed = enqueue_job(
        session,
        project_id=project_id,
        job_type="GENERATE_STORYBOARD_V2",
        entity_type="visual_bible_version",
        entity_id=visual_bible.id,
        idempotency_key=f"{project_id}:GENERATE_STORYBOARD_V2:{visual_bible.id}:v1",
        input_payload={
            "project_id": project_id,
            "visual_bible_version_id": visual_bible.id,
            "config_version": "storyboard-v2",
        },
        label=f"{project.name} · 动态分镜",
        stage="等待从批准剧本生成动态分镜",
        trace_id=trace_id,
        estimated_seconds=12,
        retryable=True,
    )
    append_event(
        session,
        project_id=project_id,
        job_id=job.id,
        event_type="preproduction.approved",
        payload={"visual_bible_version_id": visual_bible.id},
    )
    session.flush()
    if commit:
        session.commit()
    session.refresh(job)
    return (
        {
            "id": visual_bible.id,
            "version": visual_bible.version,
            "status": visual_bible.status,
            "content_hash": visual_bible.content_hash,
        },
        job_to_read(job),
        replayed,
    )
