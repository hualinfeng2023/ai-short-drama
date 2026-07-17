import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import Base
from app.db.models import Asset, BriefVersion, IdempotencyKey, Project
from app.domain.statuses import ProjectStatus
from app.schemas import (
    BriefVersionRead,
    ProjectCreate,
    ProjectCreateResult,
    ProjectRead,
    ProjectUpdate,
    ProjectUpdateResult,
)
from app.services.project_naming import suggest_project_name
from app.services.workspace import project_or_404

CREATE_SCOPE = "POST:/api/v1/projects"
EDITABLE_PROJECT_STATES = {ProjectStatus.DRAFT, ProjectStatus.PROPOSAL_READY}


def delete_project(session: Session, project_id: str) -> dict[str, object]:
    project = project_or_404(session, project_id)
    storage_keys = set(
        session.scalars(select(Asset.storage_key).where(Asset.project_id == project_id)).all()
    )
    shared_storage_keys = (
        set(
            session.scalars(
                select(Asset.storage_key).where(
                    Asset.storage_key.in_(storage_keys),
                    Asset.project_id != project_id,
                )
            ).all()
        )
        if storage_keys
        else set()
    )
    tables = list(Base.metadata.sorted_tables)
    owned_ids: dict[str, set[object]] = {Project.__tablename__: {project.id}}

    # Discover every row reachable through a declared foreign key. This keeps deletion
    # complete as new project-owned version tables are added to the workflow.
    for _ in range(len(tables)):
        changed = False
        for table in tables:
            if table.name == Project.__tablename__ or "id" not in table.c:
                continue
            table_ids = owned_ids.setdefault(table.name, set())
            for foreign_key in table.foreign_keys:
                parent_ids = owned_ids.get(foreign_key.column.table.name)
                if not parent_ids:
                    continue
                matches = set(
                    session.scalars(
                        select(table.c.id).where(foreign_key.parent.in_(parent_ids))
                    ).all()
                )
                new_ids = matches - table_ids
                if new_ids:
                    table_ids.update(new_ids)
                    changed = True
        if not changed:
            break

    all_owned_ids = {item for values in owned_ids.values() for item in values}
    if all_owned_ids:
        session.execute(delete(IdempotencyKey).where(IdempotencyKey.resource_id.in_(all_owned_ids)))

    deleted_rows = 0
    character_table = Base.metadata.tables["characters"]
    character_ids = owned_ids.get("characters", set())
    if character_ids:
        session.execute(
            update(character_table)
            .where(character_table.c.id.in_(character_ids))
            .values(
                current_profile_version_id=None,
                locked_identity_version_id=None,
                active_look_version_id=None,
                active_story_state_version_id=None,
            )
        )
    cyclic_tables = (
        "character_identity_assets",
        "character_story_state_versions",
        "character_look_versions",
        "character_identity_versions",
        "character_candidates",
        "character_candidate_batches",
        "character_family_resemblance_constraints",
        "character_visual_profile_versions",
        "characters",
    )
    for table_name in cyclic_tables:
        table = Base.metadata.tables[table_name]
        table_ids = owned_ids.get(table_name)
        if not table_ids:
            continue
        result = session.execute(delete(table).where(table.c.id.in_(table_ids)))
        if result.rowcount and result.rowcount > 0:
            deleted_rows += result.rowcount
    for table in reversed(tables):
        if table.name in cyclic_tables:
            continue
        table_ids = owned_ids.get(table.name)
        if not table_ids or "id" not in table.c:
            continue
        result = session.execute(delete(table).where(table.c.id.in_(table_ids)))
        if result.rowcount and result.rowcount > 0:
            deleted_rows += result.rowcount

    session.commit()
    assets_root = (get_settings().data_dir / "assets").resolve()
    deleted_files = 0
    for storage_key in storage_keys - shared_storage_keys:
        path = (get_settings().data_dir / storage_key).resolve()
        if path.is_relative_to(assets_root) and path.is_file():
            path.unlink()
            deleted_files += 1
    return {
        "project_id": project_id,
        "deleted": True,
        "deleted_rows": deleted_rows,
        "deleted_files": deleted_files,
    }


def _json_list(value: str) -> list[str]:
    parsed = json.loads(value)
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _json_object(value: str) -> dict[str, str | int | float | bool]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): item for key, item in parsed.items() if isinstance(item, str | int | float | bool)
    }


def brief_to_read(brief: BriefVersion) -> BriefVersionRead:
    return BriefVersionRead(
        id=brief.id,
        project_id=brief.project_id,
        version=brief.version,
        project_name=brief.project_name,
        raw_input=brief.raw_input,
        genre=brief.genre,
        style=brief.style,
        target_duration_sec=brief.target_duration_sec,
        aspect_ratio=brief.aspect_ratio,
        target_platform=brief.target_platform,
        reference_asset_ids=_json_list(brief.reference_asset_ids_json),
        assumptions=_json_list(brief.assumptions_json),
        narrative_protagonist=brief.narrative_protagonist,
        target_audience=brief.target_audience,
        emotional_rewards=_json_list(brief.emotional_rewards_json),
        audience_profile=brief.audience_profile,
        production_format=brief.production_format,
        primary_audience=brief.primary_audience,
        secondary_audiences=_json_list(brief.secondary_audiences_json),
        primary_market=brief.primary_market,
        secondary_markets=_json_list(brief.secondary_markets_json),
        canonical_language=brief.canonical_language,
        localization_targets=_json_list(brief.localization_targets_json),
        platform_targets=json.loads(brief.platform_targets_json),
        content_requirements=_json_list(brief.content_requirements_json),
        content_avoidances=_json_list(brief.content_avoidances_json),
        creative_defaults=_json_object(brief.creative_defaults_json),
        blocking_questions=_json_list(brief.blocking_questions_json),
        payload_schema_version=brief.payload_schema_version,
        content_hash=brief.content_hash,
        status=brief.status,
        created_at=brief.created_at,
    )


def list_brief_versions(session: Session, project_id: str) -> list[BriefVersionRead]:
    project_or_404(session, project_id)
    briefs = session.scalars(
        select(BriefVersion)
        .where(BriefVersion.project_id == project_id)
        .order_by(BriefVersion.version.desc())
    ).all()
    return [brief_to_read(brief) for brief in briefs]


def _targeting_conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "BRIEF_TARGETING_CONFLICT",
            "message": message,
            "retryable": False,
        },
    )


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: object) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def idempotency_conflict(key: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "IDEMPOTENCY_CONFLICT",
            "message": "该幂等键已用于不同的创建请求",
            "user_action": "为新的项目载荷使用新的 Idempotency-Key",
            "retryable": False,
            "details": {"idempotency_key": key},
        },
    )


def project_locked(project: Project) -> HTTPException:
    return HTTPException(
        status_code=423,
        detail={
            "code": "PROJECT_LOCKED",
            "message": "当前项目阶段不允许直接修改项目简报",
            "user_action": "在草稿阶段编辑项目，或在后续阶段创建变更集",
            "retryable": False,
            "details": {"status": project.status, "project_id": project.id},
        },
    )


def version_conflict(project: Project, expected_version: int) -> HTTPException:
    latest = ProjectRead.model_validate(project).model_dump(mode="json")
    return HTTPException(
        status_code=409,
        detail={
            "code": "VERSION_CONFLICT",
            "message": "项目已被其他修改更新",
            "user_action": "刷新最新版本后重新提交",
            "retryable": False,
            "details": {
                "expected_version": expected_version,
                "latest_version": project.lock_version,
                "latest": latest,
            },
        },
    )


def replay_create(
    record: IdempotencyKey, request_hash: str, idempotency_key: str
) -> ProjectCreateResult:
    if record.request_hash != request_hash:
        raise idempotency_conflict(idempotency_key)
    stored = json.loads(record.response_json)
    return ProjectCreateResult(
        project=ProjectRead.model_validate(stored["project"]),
        brief_version=int(stored["brief_version"]),
        idempotency_replayed=True,
    )


async def create_project(
    session: Session, payload: ProjectCreate, idempotency_key: str
) -> ProjectCreateResult:
    if payload.reference_asset_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "REFERENCE_ASSET_PROJECT_REQUIRED",
                "message": "请先创建草稿，再通过项目素材接口上传并关联参考素材",
                "retryable": False,
            },
        )
    request_data = payload.model_dump(mode="json")
    request_hash = content_hash(request_data)
    existing = session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.scope == CREATE_SCOPE,
            IdempotencyKey.key == idempotency_key,
        )
    )
    if existing is not None:
        return replay_create(existing, request_hash, idempotency_key)

    project_name = payload.name
    if project_name is None:
        session.rollback()
        suggestion = await suggest_project_name(
            {
                **payload.model_dump(mode="json"),
                "current_name": None,
            }
        )
        project_name = suggestion.suggested

    now = datetime.now(UTC)
    project = Project(
        id=str(uuid4()),
        name=project_name,
        idea=payload.idea,
        genre=payload.genre,
        style=payload.style,
        target_duration_sec=payload.target_duration_sec,
        aspect_ratio=payload.aspect_ratio,
        target_platform=payload.target_platform,
        status=ProjectStatus.DRAFT,
        lock_version=1,
        available_points=50000,
        timeline_version=0,
        preview_approved=False,
        export_ready=False,
        created_at=now,
        updated_at=now,
    )
    brief_payload = {
        "project_name": project.name,
        "raw_input": project.idea,
        "genre": project.genre,
        "style": project.style,
        "target_duration_sec": project.target_duration_sec,
        "aspect_ratio": project.aspect_ratio,
        "target_platform": project.target_platform,
        "reference_asset_ids": payload.reference_asset_ids,
        "assumptions": payload.assumptions,
        "narrative_protagonist": payload.narrative_protagonist,
        "target_audience": payload.target_audience,
        "emotional_rewards": payload.emotional_rewards,
        "audience_profile": payload.audience_profile,
        "production_format": payload.production_format,
        "primary_audience": payload.primary_audience,
        "secondary_audiences": payload.secondary_audiences,
        "primary_market": payload.primary_market,
        "secondary_markets": payload.secondary_markets,
        "canonical_language": payload.canonical_language,
        "localization_targets": payload.localization_targets,
        "platform_targets": [item.model_dump(mode="json") for item in payload.platform_targets],
        "content_requirements": payload.content_requirements,
        "content_avoidances": payload.content_avoidances,
        "creative_defaults": payload.creative_defaults,
        "blocking_questions": payload.blocking_questions,
        "payload_schema_version": "brief-v3",
    }
    brief = BriefVersion(
        id=str(uuid4()),
        project_id=project.id,
        version=1,
        project_name=project.name,
        raw_input=project.idea,
        genre=project.genre,
        style=project.style,
        target_duration_sec=project.target_duration_sec,
        aspect_ratio=project.aspect_ratio,
        target_platform=project.target_platform,
        reference_asset_ids_json=canonical_json(payload.reference_asset_ids),
        assumptions_json=canonical_json(payload.assumptions),
        narrative_protagonist=payload.narrative_protagonist,
        target_audience=payload.target_audience,
        emotional_rewards_json=canonical_json(payload.emotional_rewards),
        audience_profile=payload.audience_profile,
        production_format=payload.production_format,
        primary_audience=payload.primary_audience,
        secondary_audiences_json=canonical_json(payload.secondary_audiences),
        primary_market=payload.primary_market,
        secondary_markets_json=canonical_json(payload.secondary_markets),
        canonical_language=payload.canonical_language,
        localization_targets_json=canonical_json(payload.localization_targets),
        platform_targets_json=canonical_json(
            [item.model_dump(mode="json") for item in payload.platform_targets]
        ),
        content_requirements_json=canonical_json(payload.content_requirements),
        content_avoidances_json=canonical_json(payload.content_avoidances),
        creative_defaults_json=canonical_json(payload.creative_defaults),
        blocking_questions_json=canonical_json(payload.blocking_questions),
        payload_schema_version="brief-v3",
        content_hash=content_hash(brief_payload),
        status="DRAFT",
        created_at=now,
    )
    session.add(project)
    session.add(brief)
    session.flush()
    project_read = ProjectRead.model_validate(project)
    stored_response = canonical_json(
        {"project": project_read.model_dump(mode="json"), "brief_version": 1}
    )
    session.add(
        IdempotencyKey(
            id=str(uuid4()),
            scope=CREATE_SCOPE,
            key=idempotency_key,
            request_hash=request_hash,
            response_json=stored_response,
            status_code=201,
            resource_id=project.id,
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.scope == CREATE_SCOPE,
                IdempotencyKey.key == idempotency_key,
            )
        )
        if winner is None:
            raise
        return replay_create(winner, request_hash, idempotency_key)
    return ProjectCreateResult(
        project=project_read,
        brief_version=1,
        idempotency_replayed=False,
    )


def update_project(
    session: Session, project_id: str, payload: ProjectUpdate
) -> ProjectUpdateResult:
    project = project_or_404(session, project_id)
    if project.status not in EDITABLE_PROJECT_STATES:
        raise project_locked(project)
    if payload.expected_version != project.lock_version:
        raise version_conflict(project, payload.expected_version)

    latest_brief = session.scalar(
        select(BriefVersion)
        .where(BriefVersion.project_id == project_id)
        .order_by(BriefVersion.version.desc())
    )
    reference_asset_ids = (
        payload.reference_asset_ids
        if payload.reference_asset_ids is not None
        else json.loads(latest_brief.reference_asset_ids_json)
        if latest_brief
        else []
    )
    if reference_asset_ids:
        assets = session.scalars(
            select(Asset).where(
                Asset.id.in_(reference_asset_ids),
                Asset.project_id == project_id,
                Asset.kind.like("REFERENCE_%"),
                Asset.rights_status == "USER_CONFIRMED",
            )
        ).all()
        if {asset.id for asset in assets} != set(reference_asset_ids):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "REFERENCE_ASSET_INVALID",
                    "message": "参考素材不存在、不属于当前项目或缺少权利确认",
                    "retryable": False,
                },
            )
    assumptions = (
        payload.assumptions
        if payload.assumptions is not None
        else json.loads(latest_brief.assumptions_json)
        if latest_brief
        else []
    )
    narrative_protagonist = (
        payload.narrative_protagonist
        if payload.narrative_protagonist is not None
        else latest_brief.narrative_protagonist
        if latest_brief
        else "unspecified"
    )
    target_audience = (
        payload.target_audience
        if payload.target_audience is not None
        else latest_brief.target_audience
        if latest_brief
        else "general"
    )
    emotional_rewards = (
        payload.emotional_rewards
        if payload.emotional_rewards is not None
        else _json_list(latest_brief.emotional_rewards_json)
        if latest_brief
        else []
    )
    audience_profile = (
        payload.audience_profile
        if payload.audience_profile is not None
        else latest_brief.audience_profile
        if latest_brief
        else ""
    )
    production_format = (
        payload.production_format
        if payload.production_format is not None
        else latest_brief.production_format
        if latest_brief
        else "live_action"
    )
    primary_audience = (
        payload.primary_audience
        if payload.primary_audience is not None
        else latest_brief.primary_audience
        if latest_brief
        else "general"
    )
    secondary_audiences = (
        payload.secondary_audiences
        if payload.secondary_audiences is not None
        else _json_list(latest_brief.secondary_audiences_json)
        if latest_brief
        else []
    )
    primary_market = (
        payload.primary_market
        if payload.primary_market is not None
        else latest_brief.primary_market
        if latest_brief
        else "CN"
    )
    secondary_markets = (
        payload.secondary_markets
        if payload.secondary_markets is not None
        else _json_list(latest_brief.secondary_markets_json)
        if latest_brief
        else []
    )
    canonical_language = (
        payload.canonical_language
        if payload.canonical_language is not None
        else latest_brief.canonical_language
        if latest_brief
        else "zh-CN"
    )
    localization_targets = (
        payload.localization_targets
        if payload.localization_targets is not None
        else _json_list(latest_brief.localization_targets_json)
        if latest_brief
        else []
    )
    content_requirements = (
        payload.content_requirements
        if payload.content_requirements is not None
        else _json_list(latest_brief.content_requirements_json)
        if latest_brief
        else []
    )
    content_avoidances = (
        payload.content_avoidances
        if payload.content_avoidances is not None
        else _json_list(latest_brief.content_avoidances_json)
        if latest_brief
        else []
    )
    creative_defaults = (
        payload.creative_defaults
        if payload.creative_defaults is not None
        else _json_object(latest_brief.creative_defaults_json)
        if latest_brief
        else {}
    )
    blocking_questions = (
        payload.blocking_questions
        if payload.blocking_questions is not None
        else _json_list(latest_brief.blocking_questions_json)
        if latest_brief
        else []
    )
    if primary_audience in secondary_audiences:
        raise _targeting_conflict("主目标用户不能同时出现在次要目标用户中")
    if primary_market in secondary_markets:
        raise _targeting_conflict("主市场不能同时出现在次要市场中")
    if canonical_language in localization_targets:
        raise _targeting_conflict("规范语言不能同时出现在本地化语言中")
    project_values = {
        field: getattr(project, field)
        for field in (
            "name",
            "idea",
            "genre",
            "style",
            "target_duration_sec",
            "aspect_ratio",
            "target_platform",
        )
    }
    for field in (
        "name",
        "idea",
        "genre",
        "style",
        "target_duration_sec",
        "aspect_ratio",
        "target_platform",
    ):
        value = getattr(payload, field)
        if value is not None:
            project_values[field] = value

    if payload.platform_targets is not None:
        platform_targets = [item.model_dump(mode="json") for item in payload.platform_targets]
        primary_platform = next(
            item for item in payload.platform_targets if item.priority == "PRIMARY"
        )
        project_values["target_platform"] = primary_platform.platform
        project_values["aspect_ratio"] = primary_platform.aspect_ratio
        project_values["target_duration_sec"] = primary_platform.target_duration_sec
    elif latest_brief:
        platform_targets = json.loads(latest_brief.platform_targets_json)
        for item in platform_targets:
            if isinstance(item, dict) and item.get("priority") == "PRIMARY":
                item.update(
                    {
                        "platform": project_values["target_platform"],
                        "aspect_ratio": project_values["aspect_ratio"],
                        "target_duration_sec": project_values["target_duration_sec"],
                    }
                )
    else:
        platform_targets = [
            {
                "platform": project_values["target_platform"],
                "priority": "PRIMARY",
                "aspect_ratio": project_values["aspect_ratio"],
                "target_duration_sec": project_values["target_duration_sec"],
                "caption_mode": "BOTH",
            }
        ]

    next_brief_version = (
        session.scalar(
            select(func.max(BriefVersion.version)).where(BriefVersion.project_id == project_id)
        )
        or 0
    ) + 1
    now = datetime.now(UTC)
    result = session.execute(
        update(Project)
        .where(
            Project.id == project_id,
            Project.lock_version == payload.expected_version,
            Project.status.in_(EDITABLE_PROJECT_STATES),
        )
        .values(
            **project_values,
            lock_version=payload.expected_version + 1,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        latest_project = project_or_404(session, project_id)
        if latest_project.status not in EDITABLE_PROJECT_STATES:
            raise project_locked(latest_project)
        raise version_conflict(latest_project, payload.expected_version)
    session.refresh(project)
    brief_payload = {
        "project_name": project.name,
        "raw_input": project.idea,
        "genre": project.genre,
        "style": project.style,
        "target_duration_sec": project.target_duration_sec,
        "aspect_ratio": project.aspect_ratio,
        "target_platform": project.target_platform,
        "reference_asset_ids": reference_asset_ids,
        "assumptions": assumptions,
        "narrative_protagonist": narrative_protagonist,
        "target_audience": target_audience,
        "emotional_rewards": emotional_rewards,
        "audience_profile": audience_profile,
        "production_format": production_format,
        "primary_audience": primary_audience,
        "secondary_audiences": secondary_audiences,
        "primary_market": primary_market,
        "secondary_markets": secondary_markets,
        "canonical_language": canonical_language,
        "localization_targets": localization_targets,
        "platform_targets": platform_targets,
        "content_requirements": content_requirements,
        "content_avoidances": content_avoidances,
        "creative_defaults": creative_defaults,
        "blocking_questions": blocking_questions,
        "payload_schema_version": "brief-v3",
    }
    session.add(
        BriefVersion(
            id=str(uuid4()),
            project_id=project.id,
            version=next_brief_version,
            project_name=project.name,
            raw_input=project.idea,
            genre=project.genre,
            style=project.style,
            target_duration_sec=project.target_duration_sec,
            aspect_ratio=project.aspect_ratio,
            target_platform=project.target_platform,
            reference_asset_ids_json=canonical_json(reference_asset_ids),
            assumptions_json=canonical_json(assumptions),
            narrative_protagonist=narrative_protagonist,
            target_audience=target_audience,
            emotional_rewards_json=canonical_json(emotional_rewards),
            audience_profile=audience_profile,
            production_format=production_format,
            primary_audience=primary_audience,
            secondary_audiences_json=canonical_json(secondary_audiences),
            primary_market=primary_market,
            secondary_markets_json=canonical_json(secondary_markets),
            canonical_language=canonical_language,
            localization_targets_json=canonical_json(localization_targets),
            platform_targets_json=canonical_json(platform_targets),
            content_requirements_json=canonical_json(content_requirements),
            content_avoidances_json=canonical_json(content_avoidances),
            creative_defaults_json=canonical_json(creative_defaults),
            blocking_questions_json=canonical_json(blocking_questions),
            payload_schema_version="brief-v3",
            content_hash=content_hash(brief_payload),
            status="DRAFT",
            created_at=now,
        )
    )
    session.commit()
    session.refresh(project)
    return ProjectUpdateResult(
        project=ProjectRead.model_validate(project), brief_version=next_brief_version
    )
