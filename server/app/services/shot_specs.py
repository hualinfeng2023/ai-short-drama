import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterIdentityVersion,
    CharacterLookVersion,
    CharacterStoryStateVersion,
    Episode,
    LocationVersion,
    Project,
    PropVersion,
    Scene,
    Shot,
    ShotConstraintLock,
    StoryBibleVersion,
    StoryboardVersion,
)
from app.db.models import (
    ShotSpec as ShotSpecRecord,
)
from app.domain.shot_spec import (
    SHOT_SPEC_EDITABLE_FIELDS,
    ArtDirectionSpec,
    AudioSpec,
    CameraSpec,
    CharacterPerformance,
    CharacterState,
    ContinuitySpec,
    GenerationSpec,
    LightingSpec,
    PerformanceSpec,
    PropState,
    ShotSource,
    ShotSpec,
    ShotState,
    TechniqueSpec,
    VisualContent,
)
from app.services.projects import canonical_json, content_hash
from app.services.prompt_compiler import CompiledPrompt, PromptCompiler
from app.services.provenance import record_shot_spec_revision
from app.services.shot_validator import ShotValidationReport, ShotValidator

_SEQUENCE_BUDGET_ISSUE_CODES = {
    "TOTAL_DURATION_MISMATCH",
    "SCENE_DURATION_MISMATCH",
}


def _json(raw: object, fallback: object) -> object:
    if not isinstance(raw, str):
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _idea_section(idea: str, label: str, end_labels: tuple[str, ...]) -> str:
    match = re.search(rf"{re.escape(label)}\s*[：:]?\s*", idea or "")
    if match is None:
        return ""
    start = match.end()
    end = len(idea)
    for end_label in end_labels:
        next_match = re.search(
            rf"\n\s*{re.escape(end_label)}\s*[：:]?",
            idea[start:],
        )
        if next_match is not None:
            end = min(end, start + next_match.start())
    return re.sub(r"\s+", " ", idea[start:end]).strip()


def _project_visual_inheritance(
    session: Session,
    project: Project,
) -> dict[str, object]:
    story_bible = session.scalar(
        select(StoryBibleVersion)
        .where(
            StoryBibleVersion.project_id == project.id,
            StoryBibleVersion.status == "APPROVED",
        )
        .order_by(StoryBibleVersion.version.desc())
    )
    payload = (
        _json(story_bible.payload_json, {})
        if story_bible is not None
        else {}
    )
    payload = payload if isinstance(payload, dict) else {}
    world = payload.get("world")
    if not isinstance(world, str) or not world.strip():
        world = _idea_section(
            project.idea,
            "核心设定",
            ("故事梗概", "主要角色", "整体视觉", "分镜", "分镜01"),
        )
    visual_direction: dict[str, object] = {}
    overall_visual = _idea_section(
        project.idea,
        "整体视觉",
        ("分镜", "分镜01", "主要角色", "故事梗概"),
    )
    if overall_visual:
        visual_direction["overall_visual"] = overall_visual
    return {
        "story_bible_version_id": story_bible.id if story_bible is not None else None,
        "story_bible_content_hash": (
            story_bible.content_hash if story_bible is not None else None
        ),
        "world": world or "",
        "visual_direction": visual_direction,
    }


def _version_visual_payload(record: object | None, field: str) -> dict[str, object]:
    if record is None:
        return {}
    raw = getattr(record, field, "{}")
    value = _json(raw, {})
    return value if isinstance(value, dict) else {}


def _project_and_scene(
    session: Session, spec: ShotSpecRecord
) -> tuple[Project, Scene, Shot]:
    shot = session.get(Shot, spec.shot_id)
    if shot is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "SHOT_SPEC_SHOT_MISSING", "message": "镜头规格关联的镜头不存在"},
        )
    scene = session.get(Scene, shot.scene_id)
    project = (
        session.scalar(
            select(Project)
            .join(Episode, Episode.project_id == Project.id)
            .where(Episode.id == scene.episode_id)
        )
        if scene is not None
        else None
    )
    if project is None or scene is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "SHOT_SPEC_PROJECT_MISSING", "message": "镜头规格未关联到有效项目"},
        )
    return project, scene, shot


def build_structured_shot_spec(
    *,
    duration_sec: float,
    narrative_goal: str,
    description: str,
    action: str,
    environment: str,
    location: str,
    time_of_day: str,
    shot_size: str,
    camera_movement: str,
    character_ids: list[str],
    character_names: list[str],
    prop_version_ids: list[str],
    prop_names: list[str],
    location_version_id: str | None,
    dialogue: str,
    delivery: str,
    project_style: str,
    aspect_ratio: str,
    source_scene_ordinal: int,
    source_script_scene_id: str,
    source_script_line_ids: list[str],
    code: str,
    title: str,
    reference_asset_ids: list[str],
    lighting_style: str | None = None,
    art_palette: list[str] | None = None,
) -> ShotSpec:
    character_states = [
        CharacterState(
            character_id=character_id,
            name=character_names[index] if index < len(character_names) else "",
            position="按画面构图站位",
            pose="执行本镜头动作",
            emotion="延续场景情绪",
            wardrobe="严格沿用角色锁定造型",
            screen_direction="NONE",
        )
        for index, character_id in enumerate(character_ids)
    ]
    prop_states = [
        PropState(
            prop_id=prop_id,
            name=prop_names[index] if index < len(prop_names) else "",
            state="保持当前连续性状态",
            position="按画面构图摆放",
        )
        for index, prop_id in enumerate(prop_version_ids)
    ]
    state = ShotState(
        location=location or "未标注场景",
        time_of_day=time_of_day or "未标注时段",
        characters=character_states,
        props=prop_states,
        action_state=f"动作开始：{action}",
        environment_state=environment or location or "场景状态保持稳定",
    )
    performances = [
        CharacterPerformance(
            character_id=character_id,
            action=action,
            emotion="延续场景情绪并完成叙事目标",
            blocking="按构图完成站位与动作",
            dialogue=dialogue if index == 0 and delivery == "DIALOGUE" else "",
        )
        for index, character_id in enumerate(character_ids)
    ]
    return ShotSpec(
        duration_sec=duration_sec,
        narrative_goal=narrative_goal or "推进当前场景叙事",
        visual_content=VisualContent(
            description=description or action or "完成当前镜头动作",
            subjects=character_names,
            action=action or description or "保持画面状态",
            environment=environment or location or "当前场景",
            composition=f"{shot_size} 景别，主体关系清晰",
            visible_props=prop_names,
        ),
        start_state=state,
        end_state=state.model_copy(
            update={"action_state": f"动作完成：{action or description}"}
        ),
        camera=CameraSpec(
            shot_size=shot_size,
            movement=camera_movement,
            angle="平视",
            framing=f"{shot_size} 景别，保持主体与环境关系",
            lens_mm={"EWS": 24, "WS": 28, "MS": 50, "MCU": 65, "CU": 85, "ECU": 100}.get(
                shot_size, 50
            ),
            focus="主体清晰，景深服务叙事重点",
            axis_id=f"scene-{source_scene_ordinal}",
            axis_side="NEUTRAL",
        ),
        lighting=LightingSpec(
            style=lighting_style or f"{time_of_day}电影叙事光",
            key_light="主光方向与场景空间关系一致",
            fill_light="控制暗部层次，避免压平主体",
            color_temperature="服从场景时段与项目色彩设计",
            contrast="电影感中高对比",
            atmosphere="光线与空气层次服务场景情绪",
        ),
        art_direction=ArtDirectionSpec(
            visual_style=project_style or "写实电影风格",
            palette=art_palette or ["项目主色", "场景强调色"],
            texture="真实材质与细腻电影颗粒",
            production_design="沿用锁定场景、道具和项目美术体系",
            wardrobe="严格沿用角色 Lock",
            references=[],
        ),
        technique=TechniqueSpec(
            pacing=f"{duration_sec:g} 秒内完成单一叙事动作",
            transition_in="直接切入",
            transition_out="动作完成后切出",
            practical_effects=[],
            vfx=[],
            notes="保持单镜头可执行性",
        ),
        performance=PerformanceSpec(
            characters=performances,
            ensemble_blocking="角色站位与视线关系服从画面构图",
            emotion_arc="从起始情绪推进到叙事动作完成后的状态",
            notes="表演克制、可读，不增加未声明动作",
        ),
        audio=AudioSpec(
            dialogue=dialogue if delivery == "DIALOGUE" else "",
            voice_over=dialogue if delivery == "VOICE_OVER" else "",
            ambience=[],
            sfx=[],
            music="",
            sync_notes="对白、动作与音效按镜头时长同步",
        ),
        continuity=ContinuitySpec(
            character_ids=character_ids,
            prop_version_ids=prop_version_ids,
            location_version_id=location_version_id,
            previous_shot_id=None,
            must_match=["角色身份", "服装", "场景", "道具", "镜头轴线"],
            axis_notes="同场镜头保持轴线一致",
        ),
        generation=GenerationSpec(
            adapter="generic",
            model="",
            aspect_ratio=aspect_ratio,
            resolution="2K",
            fps=24,
            negative_prompt="文字、字幕、边框、拼贴、身份漂移、闪烁、肢体断裂",
            reference_asset_ids=reference_asset_ids,
            risk_flags=[],
            model_parameters={},
        ),
        source=ShotSource(
            scene_ordinal=source_scene_ordinal,
            script_scene_id=source_script_scene_id,
            script_line_ids=source_script_line_ids,
            code=code,
            title=title or code,
        ),
    )


def _legacy_contract(session: Session, spec: ShotSpecRecord) -> ShotSpec:
    project, scene, shot = _project_and_scene(session, spec)
    prompt = _json(spec.prompt_json, {})
    prompt = prompt if isinstance(prompt, dict) else {}
    character_ids = _json(shot.character_ids_json, [])
    character_ids = (
        [item for item in character_ids if isinstance(item, str)]
        if isinstance(character_ids, list)
        else []
    )
    characters = (
        list(session.scalars(select(Character).where(Character.id.in_(character_ids))).all())
        if character_ids
        else []
    )
    names_by_id = {item.id: item.name for item in characters}
    prop_ids = _json(spec.prop_version_ids_json, [])
    prop_ids = (
        [item for item in prop_ids if isinstance(item, str)]
        if isinstance(prop_ids, list)
        else []
    )
    source_line_ids = _json(spec.script_line_ids_json, [])
    source_line_ids = (
        [item for item in source_line_ids if isinstance(item, str)]
        if isinstance(source_line_ids, list)
        else []
    )
    return build_structured_shot_spec(
        duration_sec=max(0.5, spec.duration_ms / 1000),
        narrative_goal=scene.purpose,
        description=spec.description,
        action=str(prompt.get("visual_action") or spec.description),
        environment=shot.location,
        location=shot.location,
        time_of_day=shot.time_of_day,
        shot_size=spec.shot_size,
        camera_movement=spec.camera_movement,
        character_ids=character_ids,
        character_names=[names_by_id.get(item, "") for item in character_ids],
        prop_version_ids=prop_ids,
        prop_names=[],
        location_version_id=spec.location_version_id,
        dialogue=spec.dialogue,
        delivery=str(prompt.get("delivery") or ("DIALOGUE" if spec.dialogue else "ACTION")),
        project_style=project.style,
        aspect_ratio=project.aspect_ratio,
        source_scene_ordinal=scene.ordinal,
        source_script_scene_id=spec.script_scene_id,
        source_script_line_ids=source_line_ids or [f"legacy:{spec.shot_id}"],
        code=shot.code,
        title=shot.title,
        reference_asset_ids=[
            item
            for item in prompt.get("reference_asset_ids", [])
            if isinstance(item, str)
        ]
        if isinstance(prompt.get("reference_asset_ids"), list)
        else [],
    )


def load_shot_spec_contract(session: Session, spec: ShotSpecRecord) -> ShotSpec:
    payload = _json(spec.structured_spec_json, {})
    if isinstance(payload, dict) and payload:
        try:
            return ShotSpec.model_validate(payload)
        except ValidationError:
            pass
    return _legacy_contract(session, spec)


def _field_value(payload: dict[str, object], field_path: str) -> object:
    current: object = payload
    for part in field_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(field_path)
        current = current[part]
    return current


def _set_field_value(payload: dict[str, object], field_path: str, value: object) -> None:
    parts = field_path.split(".")
    current = payload
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            raise KeyError(field_path)
        current = next_value
    if parts[-1] not in current:
        raise KeyError(field_path)
    current[parts[-1]] = copy.deepcopy(value)


def resolve_shot_constraints(
    session: Session,
    spec: ShotSpecRecord,
    contract: ShotSpec,
) -> tuple[ShotSpec, dict[str, object]]:
    project, scene, _shot = _project_and_scene(session, spec)
    targets = {
        "PROJECT": project.id,
        "SCENE": scene.id,
        "FIELD": spec.id,
    }
    locks = list(
        session.scalars(
            select(ShotConstraintLock).where(
                ShotConstraintLock.project_id == project.id,
                ShotConstraintLock.scope.in_(targets),
            )
        ).all()
    )
    applicable = [item for item in locks if targets.get(item.scope) == item.target_id]
    precedence = {"PROJECT": 1, "SCENE": 2, "FIELD": 3}
    payload = contract.model_dump(mode="json")
    resolutions: list[dict[str, object]] = []
    effective_by_path: dict[str, dict[str, object]] = {}
    for lock in sorted(applicable, key=lambda item: (precedence[item.scope], item.version)):
        try:
            value = json.loads(lock.value_json)
            before = _field_value(payload, lock.field_path)
            _set_field_value(payload, lock.field_path, value)
        except (json.JSONDecodeError, KeyError):
            continue
        resolution = {
            "id": lock.id,
            "scope": lock.scope,
            "target_id": lock.target_id,
            "field_path": lock.field_path,
            "value": value,
            "owner": lock.owner,
            "version": lock.version,
            "overrode_value": before,
            "effective": True,
        }
        previous = effective_by_path.get(lock.field_path)
        if previous is not None:
            previous["effective"] = False
            resolution["conflict_with"] = previous["id"]
        effective_by_path[lock.field_path] = resolution
        resolutions.append(resolution)
    resolved = ShotSpec.model_validate(payload)
    character_ids = resolved.continuity.character_ids
    characters = (
        list(session.scalars(select(Character).where(Character.id.in_(character_ids))).all())
        if character_ids
        else []
    )
    character_locks = [
        {
            "character_id": item.id,
            "name": item.name,
            "visual_brief": item.visual_brief,
            "lock_version": item.lock_version,
            "identity_version_id": item.locked_identity_version_id,
            "look_version_id": item.active_look_version_id,
            "story_state_version_id": item.active_story_state_version_id,
            "identity": {
                "stable_traits": _version_visual_payload(
                    session.get(
                        CharacterIdentityVersion,
                        item.locked_identity_version_id,
                    )
                    if item.locked_identity_version_id
                    else None,
                    "stable_traits_json",
                ),
                "prompt_snapshot": _version_visual_payload(
                    session.get(
                        CharacterIdentityVersion,
                        item.locked_identity_version_id,
                    )
                    if item.locked_identity_version_id
                    else None,
                    "prompt_snapshot_json",
                ),
            },
            "look": _version_visual_payload(
                session.get(CharacterLookVersion, item.active_look_version_id)
                if item.active_look_version_id
                else None,
                "payload_json",
            ),
            "story_state": _version_visual_payload(
                session.get(
                    CharacterStoryStateVersion,
                    item.active_story_state_version_id,
                )
                if item.active_story_state_version_id
                else None,
                "payload_json",
            ),
        }
        for item in sorted(characters, key=lambda value: value.id)
    ]
    location_id = resolved.continuity.location_version_id or spec.location_version_id
    location = session.get(LocationVersion, location_id) if location_id else None
    prop_ids = resolved.continuity.prop_version_ids
    props = (
        list(session.scalars(select(PropVersion).where(PropVersion.id.in_(prop_ids))).all())
        if prop_ids
        else []
    )
    project_inheritance = _project_visual_inheritance(session, project)
    snapshot = {
        "project": {
            "id": project.id,
            "lock_version": project.lock_version,
            "genre": project.genre,
            "style": project.style,
            "aspect_ratio": project.aspect_ratio,
            "target_platform": project.target_platform,
            **project_inheritance,
        },
        "scene": {
            "id": scene.id,
            "ordinal": scene.ordinal,
            "purpose": scene.purpose,
            "duration_sec": scene.duration_sec,
            "location": (
                {
                    "id": location.id,
                    "version": location.version,
                    "name": location.name,
                    "content_hash": location.content_hash,
                    "visual_facts": _json(location.payload_json, {}),
                }
                if location is not None
                else {}
            ),
            "props": [
                {
                    "id": item.id,
                    "version": item.version,
                    "name": item.name,
                    "content_hash": item.content_hash,
                    "visual_facts": _json(item.payload_json, {}),
                }
                for item in sorted(props, key=lambda value: value.id)
            ],
        },
        "character_locks": character_locks,
        "field_locks": resolutions,
        "effective_fields": {
            path: {
                "scope": item["scope"],
                "owner": item["owner"],
                "lock_id": item["id"],
            }
            for path, item in effective_by_path.items()
        },
    }
    snapshot["content_hash"] = content_hash(snapshot)
    return resolved, snapshot


def assert_fields_unlocked(
    session: Session,
    spec: ShotSpecRecord,
    field_paths: list[str],
) -> None:
    contract = load_shot_spec_contract(session, spec)
    _resolved, snapshot = resolve_shot_constraints(session, spec, contract)
    effective = snapshot.get("effective_fields", {})
    blocked: list[dict[str, object]] = []
    if isinstance(effective, dict):
        for path in field_paths:
            top_level = path.split(".", 1)[0]
            for locked_path, source in effective.items():
                if (
                    locked_path == path
                    or locked_path == top_level
                    or path.startswith(f"{locked_path}.")
                ):
                    blocked.append({"field_path": path, "locked_by": source})
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SHOT_FIELD_LOCKED",
                "message": "镜头字段已被上游或字段锁定，不能直接修改",
                "details": {"fields": blocked},
                "user_action": "先解除对应的项目、场景或字段锁，再保存镜头规格",
            },
        )


def compile_shot_spec(
    session: Session,
    spec: ShotSpecRecord,
    *,
    adapter_name: str | None = None,
    store: bool = True,
) -> tuple[ShotSpec, ShotValidationReport, CompiledPrompt, dict[str, object]]:
    contract = load_shot_spec_contract(session, spec)
    resolved, snapshot = resolve_shot_constraints(session, spec, contract)
    report = ShotValidator().validate_sequence([resolved])
    compiled = PromptCompiler().compile(
        resolved,
        adapter_name=adapter_name,
        project_lock=snapshot["project"],
        scene_lock=snapshot["scene"],
        character_locks=snapshot["character_locks"],
        field_locks=snapshot["field_locks"],
    )
    if store:
        spec.structured_spec_json = canonical_json(contract.model_dump(mode="json"))
        spec.prompt_compiled = compiled.prompt
        spec.prompt_adapter = compiled.adapter
        spec.compiler_version = compiled.compiler_version
        spec.compiler_input_hash = compiled.compiler_input_hash
        spec.prompt_compiled_hash = compiled.prompt_hash
        spec.validation_report_json = canonical_json(report.model_dump(mode="json"))
        spec.lock_snapshot_json = canonical_json(snapshot)
        spec.review_status = "NEEDS_REVIEW" if report.needs_review else "VALID"
        prompt_payload = _json(spec.prompt_json, {})
        prompt_payload = prompt_payload if isinstance(prompt_payload, dict) else {}
        prompt_payload.update(
            {
                "image_prompt": compiled.prompt,
                "prompt_compiled": compiled.prompt,
                "prompt_adapter": compiled.adapter,
                "compiler_input_hash": compiled.compiler_input_hash,
                "structured_spec_hash": content_hash(contract.model_dump(mode="json")),
            }
        )
        spec.prompt_json = canonical_json(prompt_payload)
        spec.content_hash = content_hash(contract.model_dump(mode="json"))
    return resolved, report, compiled, snapshot


def validate_storyboard_shot_specs(
    session: Session,
    storyboard_version_id: str,
) -> dict[str, ShotValidationReport]:
    records = list(
        session.scalars(
            select(ShotSpecRecord)
            .where(ShotSpecRecord.storyboard_version_id == storyboard_version_id)
            .order_by(ShotSpecRecord.ordinal)
        ).all()
    )
    if not records:
        return {}
    storyboard = session.get(StoryboardVersion, storyboard_version_id)
    episode = (
        session.get(Episode, storyboard.episode_id)
        if storyboard is not None and storyboard.episode_id
        else None
    )
    resolved = [
        resolve_shot_constraints(session, record, load_shot_spec_contract(session, record))[0]
        for record in records
    ]
    scene_durations: dict[int, float] = {}
    for record, contract in zip(records, resolved, strict=True):
        shot = session.get(Shot, record.shot_id)
        scene = session.get(Scene, shot.scene_id) if shot is not None else None
        if scene is not None:
            scene_durations[contract.source.scene_ordinal] = float(scene.duration_sec)
    sequence_report = ShotValidator().validate_sequence(
        resolved,
        expected_total_duration_sec=(
            float(episode.target_duration_sec) if episode is not None else None
        ),
        expected_scene_durations=scene_durations or None,
    )
    reports: dict[str, ShotValidationReport] = {}
    for index, (record, contract) in enumerate(zip(records, resolved, strict=True)):
        shot_prefix = f"shots.{index}"
        scene_prefix = f"scene.{contract.source.scene_ordinal}"
        issues = [
            issue
            for issue in sequence_report.issues
            if issue.field_path == shot_prefix
            or issue.field_path.startswith(f"{shot_prefix}.")
            or issue.field_path == scene_prefix
            or (index == 0 and issue.field_path == "shots")
        ]
        needs_review = any(issue.severity == "BLOCKER" for issue in issues)
        report = ShotValidationReport(
            valid=not needs_review,
            needs_review=needs_review,
            total_duration_sec=sequence_report.total_duration_sec,
            issues=issues,
        )
        reports[record.id] = report
        record.validation_report_json = canonical_json(report.model_dump(mode="json"))
        record.review_status = "NEEDS_REVIEW" if needs_review else "VALID"
        shot = session.get(Shot, record.shot_id)
        previous_shot_status = shot.status if shot is not None else None
        if needs_review:
            if record.status != "APPROVED":
                record.status = "NEEDS_REVIEW"
            if shot is not None and shot.status != "APPROVED":
                shot.status = "NEEDS_REVIEW"
        elif record.status == "NEEDS_REVIEW":
            record.status = "DRAFT"
            if shot is not None and shot.status == "NEEDS_REVIEW":
                shot.status = "DRAFT"
        if (
            shot is not None
            and previous_shot_status is not None
            and shot.status != previous_shot_status
        ):
            shot.lock_version += 1
    return reports


def write_shot_spec(
    session: Session,
    spec: ShotSpecRecord,
    contract: ShotSpec,
    *,
    actor: str,
    change_reason: str,
    trace_id: str | None = None,
    repair_attempts: int = 0,
    force_needs_review: bool = False,
    adapter_name: str | None = None,
) -> tuple[ShotSpecRecord, ShotValidationReport, CompiledPrompt]:
    project, _scene, shot = _project_and_scene(session, spec)
    previous = load_shot_spec_contract(session, spec)
    changed_fields = [
        field
        for field in SHOT_SPEC_EDITABLE_FIELDS
        if getattr(previous, field) != getattr(contract, field)
    ]
    if changed_fields:
        assert_fields_unlocked(session, spec, changed_fields)
    spec.structured_spec_json = canonical_json(contract.model_dump(mode="json"))
    spec.repair_attempts = repair_attempts
    spec.description = contract.visual_content.description
    spec.dialogue = contract.audio.dialogue or contract.audio.voice_over
    spec.duration_ms = round(contract.duration_sec * 1000)
    spec.shot_size = contract.camera.shot_size
    spec.camera_movement = contract.camera.movement
    spec.location_version_id = contract.continuity.location_version_id
    spec.prop_version_ids_json = canonical_json(contract.continuity.prop_version_ids)
    shot.description = spec.description
    shot.dialogue = spec.dialogue
    shot.duration_sec = max(1, round(contract.duration_sec))
    shot.shot_size = spec.shot_size
    shot.camera_movement = spec.camera_movement
    shot.location = contract.start_state.location
    shot.time_of_day = contract.start_state.time_of_day
    _resolved, report, compiled, _snapshot = compile_shot_spec(
        session,
        spec,
        adapter_name=adapter_name,
        store=True,
    )
    report = validate_storyboard_shot_specs(session, spec.storyboard_version_id).get(
        spec.id,
        report,
    )
    if force_needs_review:
        spec.review_status = "NEEDS_REVIEW"
        report = report.model_copy(update={"valid": False, "needs_review": True})
        spec.validation_report_json = canonical_json(report.model_dump(mode="json"))
    spec.status = "DRAFT" if spec.review_status == "VALID" else "NEEDS_REVIEW"
    spec.content_hash = content_hash(contract.model_dump(mode="json"))
    shot.status = spec.status
    shot.lock_version += 1
    record_shot_spec_revision(
        session,
        project_id=project.id,
        spec=spec,
        actor=actor,
        change_reason=change_reason,
        changes={
            "changed_fields": changed_fields,
            "structured_spec_hash": spec.content_hash,
            "prompt_compiled_hash": compiled.prompt_hash,
            "review_status": spec.review_status,
        },
        trace_id=trace_id,
    )
    session.flush()
    return spec, report, compiled


def ensure_shot_spec_generation_ready(spec: ShotSpecRecord) -> None:
    if spec.review_status != "NEEDS_REVIEW":
        return
    validation_report = _json(spec.validation_report_json, {})
    raw_issues = (
        validation_report.get("issues")
        if isinstance(validation_report, dict)
        else None
    )
    generation_blockers = [
        issue
        for issue in raw_issues or []
        if isinstance(issue, dict)
        and issue.get("severity") == "BLOCKER"
        and issue.get("code") not in _SEQUENCE_BUDGET_ISSUE_CODES
    ]
    if raw_issues and not generation_blockers:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "SHOT_SPEC_NEEDS_REVIEW",
            "message": "镜头规格仍有阻断问题，不能进入生成队列",
            "details": {
                "shot_spec_id": spec.id,
                "validation_report": validation_report,
            },
            "user_action": "在导演级分镜表中修正阻断项并重新保存",
        },
    )


def upsert_shot_constraint_lock(
    session: Session,
    *,
    project_id: str,
    scope: str,
    target_id: str,
    field_path: str,
    value: object,
    owner: str,
) -> ShotConstraintLock:
    if field_path.split(".", 1)[0] not in SHOT_SPEC_EDITABLE_FIELDS:
        raise HTTPException(
            status_code=422,
            detail={"code": "SHOT_LOCK_FIELD_INVALID", "message": "不支持锁定该镜头字段"},
        )
    existing = session.scalar(
        select(ShotConstraintLock).where(
            ShotConstraintLock.project_id == project_id,
            ShotConstraintLock.scope == scope,
            ShotConstraintLock.target_id == target_id,
            ShotConstraintLock.field_path == field_path,
        )
    )
    now = datetime.now(UTC)
    if existing is None:
        existing = ShotConstraintLock(
            id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"shot-lock:{project_id}:{scope}:{target_id}:{field_path}",
                )
            ),
            project_id=project_id,
            scope=scope,
            target_id=target_id,
            field_path=field_path,
            value_json=canonical_json(value),
            owner=owner,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
    else:
        existing.value_json = canonical_json(value)
        existing.owner = owner
        existing.version += 1
        existing.updated_at = now
    return existing


def remove_shot_constraint_lock(
    session: Session,
    *,
    project_id: str,
    scope: str,
    target_id: str,
    field_path: str,
) -> bool:
    lock = session.scalar(
        select(ShotConstraintLock).where(
            ShotConstraintLock.project_id == project_id,
            ShotConstraintLock.scope == scope,
            ShotConstraintLock.target_id == target_id,
            ShotConstraintLock.field_path == field_path,
        )
    )
    if lock is None:
        return False
    session.delete(lock)
    return True


def compatibility_prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
