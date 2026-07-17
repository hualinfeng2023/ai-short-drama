import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    Character,
    CharacterCandidate,
    CharacterLookVersion,
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
from app.services.assets import register_file
from app.services.events import append_event
from app.services.image_provider import GeneratedImage
from app.services.jobs import enqueue_job, job_to_read
from app.services.production import list_characters
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.workspace import project_or_404


def _approved_script(session: Session, project_id: str) -> ScriptVersion | None:
    return session.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id, ScriptVersion.status == "APPROVED")
        .order_by(ScriptVersion.version.desc())
    )


def is_script_preproduction(session: Session, project_id: str) -> bool:
    return _approved_script(session, project_id) is not None


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
) -> tuple[Asset, CharacterCandidate]:
    payload = json.loads(job.input_json)
    character = session.get(Character, str(payload["character_id"]))
    if character is None:
        raise ValueError("角色不存在")
    ordinal = int(payload["ordinal"])
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
    asset.metadata_json = canonical_json(
        {
            "model": image.model,
            "provider_request_id": image.request_id,
            "source_url": image.source_url,
            "seed": payload["seed"],
        }
    )
    now = datetime.now(UTC)
    candidate = CharacterCandidate(
        id=candidate_id,
        project_id=job.project_id,
        character_id=character.id,
        ordinal=ordinal,
        asset_id=asset.id,
        seed=str(payload["seed"]),
        status="READY",
        selected=False,
        created_at=now,
    )
    session.add(candidate)
    session.flush()
    ready_count = session.scalar(
        select(func.count(CharacterCandidate.id)).where(
            CharacterCandidate.character_id == character.id
        )
    )
    if ready_count >= int(payload["candidate_count"]):
        character.status = "CANDIDATES_READY"
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


def lock_character_for_preproduction(
    session: Session,
    *,
    project: Project,
    character: Character,
    candidate: CharacterCandidate,
    expected_version: int,
    actor: str,
    trace_id: str,
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
    looks = session.scalars(
        select(CharacterLookVersion)
        .where(CharacterLookVersion.project_id == project_id)
        .order_by(CharacterLookVersion.character_id, CharacterLookVersion.version)
    ).all()
    locations = session.scalars(
        select(LocationVersion).where(LocationVersion.project_id == project_id)
    ).all()
    props = session.scalars(select(PropVersion).where(PropVersion.project_id == project_id)).all()
    voices = session.scalars(
        select(VoiceProfile).where(VoiceProfile.project_id == project_id)
    ).all()
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
                "payload": json.loads(item.payload_json),
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
