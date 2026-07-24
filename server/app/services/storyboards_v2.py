import base64
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    Character,
    CharacterCandidate,
    CharacterIdentityAsset,
    CharacterLookVersion,
    Episode,
    Job,
    JobDependency,
    LocationVersion,
    Project,
    PropVersion,
    ReviewGate,
    Scene,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    Shot,
    ShotSpec,
    StoryboardVersion,
    Take,
    VisualBibleVersion,
    WorkflowNode,
    WorkflowRun,
)
from app.schemas import JobRead
from app.services.assets import register_file, resolve_asset_path
from app.services.character_image_qc import detect_lower_right_watermark
from app.services.events import append_event
from app.services.generation_records import ensure_generation_record
from app.services.image_provider import GeneratedImage
from app.services.jobs import enqueue_job, job_to_read
from app.services.media import PreviewFiles, PreviewShot
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.workspace import project_or_404


def _split_scene_seconds(total_seconds: int, weights: list[int]) -> list[int]:
    if not weights:
        return []
    weight_total = max(1, sum(weights))
    raw = [total_seconds * weight / weight_total for weight in weights]
    values = [max(1, int(item)) for item in raw]
    delta = total_seconds - sum(values)
    if delta > 0:
        order = sorted(
            range(len(raw)),
            key=lambda index: raw[index] - int(raw[index]),
            reverse=True,
        )
        for offset in range(delta):
            values[order[offset % len(order)]] += 1
    elif delta < 0:
        order = sorted(range(len(values)), key=lambda index: values[index], reverse=True)
        for offset in range(-delta):
            index = order[offset % len(order)]
            if values[index] > 1:
                values[index] -= 1
    return values


def _mentioned_character_keys(
    text: str,
    characters_by_key: dict[str, Character],
) -> list[str]:
    mentioned: list[str] = []
    for character_key, character in characters_by_key.items():
        if character.name in text or character_key in text:
            mentioned.append(character_key)
    return mentioned


def _scene_character_keys(
    lines: list[ScriptLine],
    characters_by_key: dict[str, Character],
) -> list[str]:
    result: list[str] = []
    for line in lines:
        candidates = [line.speaker_key, *_mentioned_character_keys(line.text, characters_by_key)]
        for character_key in candidates:
            if character_key in characters_by_key and character_key not in result:
                result.append(character_key)
    return result[:8]


def _line_character_keys(
    line: ScriptLine,
    *,
    characters_by_key: dict[str, Character],
    scene_character_keys: list[str],
) -> list[str]:
    result: list[str] = []
    candidates = [line.speaker_key, *_mentioned_character_keys(line.text, characters_by_key)]
    for character_key in candidates:
        if character_key in characters_by_key and character_key not in result:
            result.append(character_key)
    if not result and line.line_type in {"ACTION", "VOICE_OVER"}:
        result.extend(scene_character_keys)
    return result[:8]


_IDENTITY_VIEW_PRIORITY = ("FRONT", "THREE_QUARTER", "FULL_BODY", "PROFILE")


def _character_reference_asset_ids(session: Session, character: Character) -> list[str]:
    """优先使用锁定身份档案正脸/全身图，回退到锁定候选图与造型参考。"""
    asset_ids: list[str] = []
    if character.locked_identity_version_id:
        identity_assets = list(
            session.scalars(
                select(CharacterIdentityAsset).where(
                    CharacterIdentityAsset.identity_version_id
                    == character.locked_identity_version_id
                )
            ).all()
        )
        by_view = {
            item.view_type: item.asset_id
            for item in identity_assets
            if item.asset_id and item.view_type != "EXPRESSIONS"
        }
        for view_type in _IDENTITY_VIEW_PRIORITY:
            asset_id = by_view.get(view_type)
            if asset_id and asset_id not in asset_ids:
                asset_ids.append(asset_id)
                break
        full_body = by_view.get("FULL_BODY")
        if full_body and full_body not in asset_ids:
            asset_ids.append(full_body)
    if not asset_ids and character.locked_candidate_id:
        candidate = session.get(CharacterCandidate, character.locked_candidate_id)
        if candidate is not None and candidate.asset_id:
            asset_ids.append(candidate.asset_id)
    if character.active_look_version_id:
        look = session.get(CharacterLookVersion, character.active_look_version_id)
        if look is not None:
            try:
                look_refs = json.loads(look.reference_asset_ids_json or "[]")
            except json.JSONDecodeError:
                look_refs = []
            if isinstance(look_refs, list):
                for item in look_refs:
                    if isinstance(item, str) and item and item not in asset_ids:
                        asset_ids.append(item)
    return asset_ids[:3]


def _storyboard_identity_prompt(characters: list[Character]) -> str:
    if not characters:
        return ""
    references = "\n".join(
        f"- 参考图对应角色：{character.name}（{character.role}）；"
        f"{character.visual_brief.strip() or '沿用锁定身份五官与发型'}"
        for character in characters
    )
    return "\n".join(
        (
            "角色身份锁定（硬约束）：",
            references,
            "- 输入参考图是每个角色唯一的身份基准。画面中的人物必须与参考图为同一人：",
            "脸型、五官比例、瞳距、鼻梁、唇形、发型核心特征、发色、年龄感与辨识度保持一致。",
            "- 允许改变表情、姿势、景别、光线与背景；禁止换脸、混脸、另造相似替身。",
            "- 当前镜头未绑定的角色不要入镜；不要新增路人抢戏。",
        )
    )


def _storyboard_photographic_direction(
    *,
    shot_size: str,
    camera_movement: str,
    time_of_day: str,
    has_dialogue: bool,
) -> str:
    shot_language = {
        "WS": "广角全景，用环境纵深交代人物位置，主体不必居中",
        "MS": "中景，人物与环境信息平衡，身体有自然重心偏移",
        "MCU": "中近景，上半身与微表情主导画面，视线可偏离镜头",
        "CU": "近景特写，焦点在眼神与呼吸，允许轻微构图失衡",
    }.get(shot_size, f"{shot_size} 景别")
    movement = {
        "STATIC": "机位克制稳定，像纪录片跟拍前的停顿",
        "PAN": "画面边缘保留运动空间，仿佛刚停住的摇镜瞬间",
        "DOLLY_IN": "透视略压缩，像推进中途截取的一帧",
        "TRACK": "背景有轻微运动模糊倾向，主体清晰",
        "HANDHELD": "极轻微手持呼吸感，避免过度晃动",
    }.get(camera_movement, camera_movement)
    time_lower = time_of_day.lower()
    if any(token in time_lower for token in ("夜", "night", "晚", "凌晨")):
        lighting = (
            "以场景实景光为主：窗光、屏幕光或顶灯形成明确方向，"
            "面部有明暗交界，拒绝平光美颜灯"
        )
    else:
        lighting = (
            "侧前方自然主光塑造体积，环境反光只补暗部，"
            "保留真实阴影与材质反光，拒绝影棚环形灯效果"
        )
    expression = (
        "人物处于说话或刚说完的间隙：口型、眼神与呼吸不同步于摆拍微笑，"
        "表情克制、有情绪残留"
        if has_dialogue
        else "表情克制自然，靠眼神与肩颈微张力传情，禁止空眼神与塑料微笑"
    )
    return (
        f"{shot_language}；{movement}。{expression}。{lighting}。"
        "按电影剧照/实拍静帧理解，而非电商肖像或 LinkedIn 头像："
        "非对称构图、前景轻微遮挡、空气透视与生活痕迹；"
        "皮肤保留毛孔与细微瑕疵，衣料有真实褶皱，景深自然。"
        "严禁：居中证件照构图、过度对称、磨皮美颜、塑料皮肤、"
        "完美打光网红脸、假笑、眼神空洞、CGI 感、插画感。"
    )


def build_storyboard_take_prompt(
    project: Project,
    *,
    description: str,
    dialogue: str,
    location: str,
    time_of_day: str,
    shot_size: str,
    camera_movement: str,
    characters: list[Character],
    aspect_ratio: str | None = None,
) -> str:
    """为低成本分镜生成身份锁定 + 写实电影静帧提示词。"""
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
    dialogue_hint = f"人物正在说：{dialogue}。" if dialogue.strip() else ""
    identity_block = _storyboard_identity_prompt(characters)
    cast_names = "、".join(character.name for character in characters) or "无具名角色"
    photo_direction = _storyboard_photographic_direction(
        shot_size=shot_size,
        camera_movement=camera_movement,
        time_of_day=time_of_day,
        has_dialogue=bool(dialogue.strip()),
    )
    return (
        f"{description.rstrip('。')}。{dialogue_hint}"
        f"出镜角色：{cast_names}。地点：{location}，时间：{time_of_day}。"
        f"{shot_size} 景别，{camera_movement} 运镜，{orientation_label} {resolved_ratio}。"
        f"{photo_direction}"
        f"整体风格延续{project.style}，色彩克制、层次丰富，避免画面文字、字幕、水印、边框和拼贴。"
        f"{identity_block}"
    )


def resolve_storyboard_take_generation_inputs(
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> tuple[str, list[str], int]:
    """在出图时重建提示词与参考图，确保沿用锁定身份而非过期 JSON 提示。"""
    shot = session.get(Shot, str(payload["shot_id"]))
    project = session.get(Project, job.project_id)
    if shot is None or project is None:
        raise ValueError("分镜任务实体不存在")
    try:
        character_ids = json.loads(shot.character_ids_json or "[]")
    except json.JSONDecodeError:
        character_ids = []
    if not isinstance(character_ids, list):
        character_ids = []
    ordered_ids = [item for item in character_ids if isinstance(item, str)]
    characters_by_id = {
        item.id: item
        for item in session.scalars(
            select(Character).where(
                Character.project_id == project.id,
                Character.id.in_(ordered_ids),
            )
        ).all()
    } if ordered_ids else {}
    characters = [
        characters_by_id[item_id] for item_id in ordered_ids if item_id in characters_by_id
    ]
    reference_asset_ids: list[str] = []
    for character in characters:
        for asset_id in _character_reference_asset_ids(session, character):
            if asset_id not in reference_asset_ids:
                reference_asset_ids.append(asset_id)
    # 若绑定角色暂无参考，回退到任务入队时携带的 reference_asset_ids
    if not reference_asset_ids:
        reference_asset_ids = [
            item for item in payload.get("reference_asset_ids", []) if isinstance(item, str)
        ]
    prompt = build_storyboard_take_prompt(
        project,
        description=shot.description,
        dialogue=shot.dialogue,
        location=shot.location,
        time_of_day=shot.time_of_day,
        shot_size=shot.shot_size,
        camera_movement=shot.camera_movement,
        characters=characters,
        aspect_ratio=project.aspect_ratio,
    )
    note = payload.get("note")
    if isinstance(note, str) and note.strip():
        prompt = f"{prompt}\n导演修改意图：{note.strip()}。"
    seed = int(payload.get("seed") or 0)
    return prompt, reference_asset_ids[:8], seed


def _create_workflow(
    session: Session,
    *,
    job: Job,
    visual_bible: VisualBibleVersion,
) -> WorkflowRun:
    existing = session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.project_id == job.project_id,
            WorkflowRun.source_entity_id == visual_bible.id,
            WorkflowRun.workflow_type == "EPISODE_PRODUCTION_V2",
        )
    )
    if existing is not None:
        return existing
    now = datetime.now(UTC)
    run = WorkflowRun(
        id=str(uuid4()),
        project_id=job.project_id,
        workflow_type="EPISODE_PRODUCTION_V2",
        source_entity_type="visual_bible_version",
        source_entity_id=visual_bible.id,
        status="RUNNING",
        current_gate=None,
        config_version="workflow-v1",
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    session.add(run)
    session.flush()
    session.add(
        WorkflowNode(
            id=str(uuid4()),
            workflow_run_id=run.id,
            node_key="storyboard.plan",
            node_type="FAN_OUT",
            entity_type="visual_bible_version",
            entity_id=visual_bible.id,
            job_id=job.id,
            status="RUNNING",
            dependency_keys_json="[]",
            output_json="{}",
            degraded=False,
            error_code=None,
            created_at=now,
            updated_at=now,
        )
    )
    return run


def create_dynamic_storyboard(session: Session, job: Job) -> tuple[StoryboardVersion, list[str]]:
    input_payload = json.loads(job.input_json)
    visual_bible = session.get(
        VisualBibleVersion,
        str(input_payload["visual_bible_version_id"]),
    )
    if visual_bible is None or visual_bible.status != "APPROVED":
        raise ValueError("批准 Visual Bible 不存在")
    script = session.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == job.project_id, ScriptVersion.status == "APPROVED")
        .order_by(ScriptVersion.version.desc())
    )
    if script is None:
        raise ValueError("已批准剧本不存在")
    existing = session.scalar(
        select(StoryboardVersion).where(
            StoryboardVersion.project_id == job.project_id,
            StoryboardVersion.script_version_id == script.id,
            StoryboardVersion.visual_bible_version_id == visual_bible.id,
        )
    )
    if existing is not None:
        child_ids = list(
            session.scalars(
                select(Job.id).where(
                    Job.project_id == job.project_id,
                    Job.job_type == "GENERATE_STORYBOARD_TAKE",
                    Job.input_json.contains(existing.id),
                )
            ).all()
        )
        return existing, child_ids
    project = project_or_404(session, job.project_id)
    workflow = _create_workflow(session, job=job, visual_bible=visual_bible)
    now = datetime.now(UTC)
    episode = Episode(
        id=str(uuid4()),
        project_id=project.id,
        code=f"S01E{script.episode_ordinal:02d}",
        ordinal=script.episode_ordinal,
        title=str(json.loads(script.payload_json)["title"]),
        target_duration_sec=round(script.estimated_duration_ms / 1000),
        status="STORYBOARDING",
    )
    session.add(episode)
    session.flush()
    storyboard_version = (
        session.scalar(
            select(func.max(StoryboardVersion.version)).where(
                StoryboardVersion.project_id == project.id,
                StoryboardVersion.episode_ordinal == script.episode_ordinal,
            )
        )
        or 0
    ) + 1
    storyboard = StoryboardVersion(
        id=str(uuid4()),
        project_id=project.id,
        script_version_id=script.id,
        visual_bible_version_id=visual_bible.id,
        workflow_run_id=workflow.id,
        episode_id=episode.id,
        episode_ordinal=script.episode_ordinal,
        version=storyboard_version,
        status="GENERATING",
        payload_json="{}",
        content_hash="",
        parent_version_id=None,
        animatic_asset_id=None,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(storyboard)
    session.flush()

    characters = list(
        session.scalars(select(Character).where(Character.project_id == project.id)).all()
    )
    characters_by_key = {item.character_key: item for item in characters}
    locations = list(
        session.scalars(
            select(LocationVersion).where(
                LocationVersion.project_id == project.id,
                LocationVersion.status == "APPROVED",
            )
        ).all()
    )
    location_by_name = {item.name: item for item in locations}
    props = list(
        session.scalars(
            select(PropVersion).where(
                PropVersion.project_id == project.id,
                PropVersion.status == "APPROVED",
            )
        ).all()
    )
    script_scenes = list(
        session.scalars(
            select(ScriptScene)
            .where(ScriptScene.script_version_id == script.id)
            .order_by(ScriptScene.ordinal)
        ).all()
    )
    child_job_ids: list[str] = []
    shot_payloads: list[dict[str, object]] = []
    shot_ordinal = 1
    for script_scene in script_scenes:
        lines = list(
            session.scalars(
                select(ScriptLine)
                .where(ScriptLine.script_scene_id == script_scene.id)
                .order_by(ScriptLine.ordinal)
            ).all()
        )
        durations = _split_scene_seconds(
            round(script_scene.duration_ms / 1000),
            [item.estimated_duration_ms + item.pause_after_ms for item in lines],
        )
        scene_character_keys = _scene_character_keys(lines, characters_by_key)
        scene = Scene(
            id=str(uuid4()),
            episode_id=episode.id,
            code=f"{script_scene.ordinal:02d}",
            ordinal=script_scene.ordinal,
            title=script_scene.heading,
            purpose=script_scene.purpose,
            duration_sec=sum(durations),
            status="STORYBOARDING",
        )
        session.add(scene)
        session.flush()
        location = location_by_name.get(script_scene.location) or (
            locations[0] if locations else None
        )
        for line, duration_sec in zip(lines, durations, strict=True):
            code = f"S{shot_ordinal:02d}"
            line_character_keys = _line_character_keys(
                line,
                characters_by_key=characters_by_key,
                scene_character_keys=scene_character_keys,
            )
            bound_characters = [characters_by_key[key] for key in line_character_keys]
            character_ids = [character.id for character in bound_characters]
            identity_ids = [
                character.locked_identity_version_id
                for character in bound_characters
                if character.locked_identity_version_id
            ]
            look_ids = [
                character.active_look_version_id
                for character in bound_characters
                if character.active_look_version_id
            ]
            story_state_ids = [
                character.active_story_state_version_id
                for character in bound_characters
                if character.active_story_state_version_id
            ]
            dialogue = line.text if line.line_type in {"DIALOGUE", "VOICE_OVER"} else ""
            speaking_character = characters_by_key.get(line.speaker_key)
            speaking_label = speaking_character.name if speaking_character is not None else "旁白"
            description = (
                f"{script_scene.purpose}。{line.text}"
                if line.line_type == "ACTION"
                else f"{speaking_label}以{line.emotion}状态完成台词，保持与锁定身份参考图为同一人"
            )
            shot_size = ("WS", "MS", "MCU", "CU")[(shot_ordinal - 1) % 4]
            camera = ("STATIC", "TRACK", "DOLLY_IN", "PAN")[(shot_ordinal - 1) % 4]
            shot = Shot(
                id=str(uuid4()),
                scene_id=scene.id,
                code=code,
                ordinal=shot_ordinal,
                title=f"{script_scene.heading} · {code}",
                description=description,
                dialogue=dialogue,
                duration_sec=duration_sec,
                status="QUEUED",
                shot_size=shot_size,
                camera_movement=camera,
                current_take=0,
                candidate_take=None,
                continuity="CLEAR",
                location=script_scene.location,
                time_of_day=script_scene.time_of_day,
                current_take_id=None,
                character_ids_json=canonical_json(character_ids),
                character_look_version="Look V1",
                character_identity_version_ids_json=canonical_json(identity_ids),
                character_look_version_ids_json=canonical_json(look_ids),
                character_story_state_version_ids_json=canonical_json(story_state_ids),
                lock_version=1,
            )
            session.add(shot)
            session.flush()
            reference_asset_ids: list[str] = []
            for character in bound_characters:
                for asset_id in _character_reference_asset_ids(session, character):
                    if asset_id not in reference_asset_ids:
                        reference_asset_ids.append(asset_id)
            image_prompt = build_storyboard_take_prompt(
                project,
                description=description,
                dialogue=dialogue,
                location=script_scene.location,
                time_of_day=script_scene.time_of_day,
                shot_size=shot_size,
                camera_movement=camera,
                characters=bound_characters,
                aspect_ratio=project.aspect_ratio,
            )
            prompt_payload = {
                "description": description,
                "dialogue": dialogue,
                "style": project.style,
                "location": script_scene.location,
                "time_of_day": script_scene.time_of_day,
                "shot_size": shot_size,
                "camera": camera,
                "character_ids": character_ids,
                "character_names": [character.name for character in bound_characters],
                "character_identity_version_ids": identity_ids,
                "character_look_ids": look_ids,
                "character_story_state_version_ids": story_state_ids,
                "location_version_id": location.id if location else None,
                "prop_version_ids": [item.id for item in props],
                "reference_asset_ids": reference_asset_ids,
                "image_prompt": image_prompt,
            }
            spec = ShotSpec(
                id=str(uuid4()),
                storyboard_version_id=storyboard.id,
                shot_id=shot.id,
                script_scene_id=script_scene.id,
                script_line_ids_json=canonical_json([line.id]),
                ordinal=shot_ordinal,
                description=description,
                dialogue=dialogue,
                duration_ms=duration_sec * 1000,
                shot_size=shot_size,
                camera_movement=camera,
                character_look_ids_json=canonical_json(look_ids),
                location_version_id=location.id if location else None,
                prop_version_ids_json=canonical_json([item.id for item in props]),
                prompt_json=canonical_json(prompt_payload),
                content_hash=content_hash(prompt_payload),
                status="QUEUED",
            )
            session.add(spec)
            session.flush()
            child, _ = enqueue_job(
                session,
                project_id=project.id,
                job_type="GENERATE_STORYBOARD_TAKE",
                entity_type="shot_spec",
                entity_id=spec.id,
                idempotency_key=f"{project.id}:GENERATE_STORYBOARD_TAKE:{spec.id}:v2",
                input_payload={
                    "storyboard_version_id": storyboard.id,
                    "workflow_run_id": workflow.id,
                    "shot_spec_id": spec.id,
                    "shot_id": shot.id,
                    "prompt": image_prompt,
                    "reference_asset_ids": reference_asset_ids,
                    "seed": int(spec.content_hash[:8], 16),
                },
                label=f"{code} · 分镜版本",
                stage="等待生成低成本分镜",
                trace_id=job.trace_id,
                estimated_seconds=30,
                retryable=True,
            )
            session.add(
                JobDependency(
                    id=str(uuid4()),
                    job_id=child.id,
                    depends_on_job_id=job.id,
                    dependency_type="SUCCESS",
                    created_at=now,
                )
            )
            session.add(
                WorkflowNode(
                    id=str(uuid4()),
                    workflow_run_id=workflow.id,
                    node_key=f"storyboard.take.{shot_ordinal}",
                    node_type="JOB",
                    entity_type="shot_spec",
                    entity_id=spec.id,
                    job_id=child.id,
                    status="READY",
                    dependency_keys_json=canonical_json(["storyboard.plan"]),
                    output_json="{}",
                    degraded=False,
                    error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            child_job_ids.append(child.id)
            shot_payloads.append(
                {
                    "shot_spec_id": spec.id,
                    "shot_id": shot.id,
                    "script_line_id": line.id,
                    "duration_ms": spec.duration_ms,
                    "content_hash": spec.content_hash,
                }
            )
            shot_ordinal += 1
    storyboard.payload_json = canonical_json(
        {
            "schema_version": "storyboard-v2",
            "script_version_id": script.id,
            "visual_bible_version_id": visual_bible.id,
            "shots": shot_payloads,
        }
    )
    storyboard.content_hash = content_hash(json.loads(storyboard.payload_json))
    root_node = session.scalar(
        select(WorkflowNode).where(
            WorkflowNode.workflow_run_id == workflow.id,
            WorkflowNode.node_key == "storyboard.plan",
        )
    )
    if root_node is not None:
        root_node.status = "FAN_OUT_COMPLETE"
        root_node.output_json = canonical_json({"child_job_ids": child_job_ids})
        root_node.updated_at = now
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="storyboard.planned",
        payload={
            "storyboard_version_id": storyboard.id,
            "shot_count": len(shot_payloads),
            "child_job_ids": child_job_ids,
        },
    )
    session.flush()
    return storyboard, child_job_ids


def reference_data_urls(
    session: Session,
    settings: Settings,
    asset_ids: list[str],
    *,
    detect_and_mask_character_watermark: bool = False,
) -> list[str]:
    values: list[str] = []
    for asset_id in asset_ids:
        asset = session.get(Asset, asset_id)
        if asset is None:
            continue
        content = resolve_asset_path(settings, asset).read_bytes()
        if detect_and_mask_character_watermark:
            content = mask_character_reference_watermark(content, asset.mime)
        values.append(f"data:{asset.mime};base64,{base64.b64encode(content).decode()}")
    return values


def mask_character_reference_watermark(content: bytes, mime: str) -> bytes:
    """Hide the known lower-right AI label only when a watermark is detected."""
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        return content
    if not detect_lower_right_watermark(content, mime):
        return content
    try:
        with Image.open(BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, UnidentifiedImageError):
        return content

    width, height = image.size
    if width < 64 or height < 64:
        return content

    left = round(width * 0.78)
    top = round(height * 0.88)
    patch_width = width - left
    patch_height = height - top
    source_top = max(0, top - patch_height)
    replacement = image.crop((left, source_top, width, top)).resize(
        (patch_width, patch_height),
        Image.Resampling.LANCZOS,
    )
    replacement = replacement.filter(
        ImageFilter.GaussianBlur(radius=max(1, round(min(patch_width, patch_height) * 0.04)))
    )

    patched = image.copy()
    patched.paste(replacement, (left, top))
    feather = max(2, round(min(width, height) * 0.006))
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rectangle(
        (left + feather, top + feather, width, height),
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
    result = Image.composite(patched, image, mask)

    output = BytesIO()
    if mime == "image/jpeg":
        result.convert("RGB").save(output, format="JPEG", quality=95, subsampling=0)
    elif mime == "image/webp":
        result.save(output, format="WEBP", quality=95)
    else:
        result.save(output, format="PNG", optimize=True)
    return output.getvalue()


def materialize_storyboard_take(
    session: Session,
    settings: Settings,
    job: Job,
    image: GeneratedImage,
) -> tuple[Asset, Take, Job | None]:
    payload = json.loads(job.input_json)
    spec = session.get(ShotSpec, str(payload["shot_spec_id"]))
    shot = session.get(Shot, str(payload["shot_id"]))
    storyboard = session.get(StoryboardVersion, str(payload["storyboard_version_id"]))
    if spec is None or shot is None or storyboard is None:
        raise ValueError("分镜任务实体不存在")
    replace_existing = bool(payload.get("replace_existing"))
    identity_refs = [
        item for item in payload.get("reference_asset_ids", []) if isinstance(item, str)
    ]

    def trace_generation(asset: Asset, take: Take, *, reused: bool) -> None:
        record = ensure_generation_record(
            session,
            job=job,
            capability="STORYBOARD_TAKE",
            provider=asset.provider,
            model=image.model,
            config_version="storyboard-take-v1",
            prompt=str(payload.get("prompt", "")),
            seed=payload.get("seed"),
            reference_asset_ids=identity_refs,
            provider_request_id=image.request_id,
            provider_task_id=None,
            output_asset_id=asset.id,
            entity_type="take",
            entity_id=take.id,
            estimated_cost_usd=0.0 if asset.provider == "mock" else None,
            metadata={
                "take_kind": take.kind,
                "take_version": take.version,
                "storyboard_version_id": storyboard.id,
                "shot_spec_id": spec.id,
                "reused_existing_output": reused,
                "replace_existing": replace_existing,
            },
        )
        take.generation_record_id = record.id

    current_take = session.scalar(
        select(Take).where(
            Take.shot_id == shot.id,
            Take.kind == "STORYBOARD",
            Take.is_current.is_(True),
        )
    )
    if current_take is not None:
        current_asset = session.get(Asset, current_take.asset_id)
        if current_asset is not None:
            try:
                metadata = json.loads(current_asset.metadata_json or "{}")
            except json.JSONDecodeError:
                metadata = {}
            # 同一 job 重试时直接返回已登记结果，避免重复造 Take
            if isinstance(metadata, dict) and metadata.get("job_id") == job.id:
                trace_generation(current_asset, current_take, reused=True)
                return current_asset, current_take, None
        if not replace_existing:
            if current_asset is None:
                raise ValueError("分镜版本资产不存在")
            trace_generation(current_asset, current_take, reused=True)
            return current_asset, current_take, None
    tmp_dir = settings.data_dir / "tmp" / job.id / "storyboard-take"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if image.mime == "image/png" else ".jpg"
    image_path = Path(tmp_dir / f"{shot.code}{suffix}")
    image_path.write_bytes(image.content)
    take_id = str(uuid4())
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind="storyboard",
        source=image_path,
        source_entity_type="take",
        source_entity_id=take_id,
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
            "storyboard_version_id": storyboard.id,
            "temporary": True,
            "job_id": job.id,
            "replace_existing": replace_existing,
            "note": payload.get("note"),
        }
    )
    now = datetime.now(UTC)
    next_version = 1
    parent_take_id = None
    if current_take is not None and replace_existing:
        current_take.is_current = False
        current_take.status = "SUPERSEDED"
        parent_take_id = current_take.id
        next_version = current_take.version + 1
    take = Take(
        id=take_id,
        shot_id=shot.id,
        kind="STORYBOARD",
        version=next_version,
        asset_id=asset.id,
        status="GENERATED",
        approval="APPROVED",
        is_current=True,
        parent_take_id=parent_take_id,
        identity_status="LOCKED_REFERENCE" if identity_refs else "NOT_APPLICABLE",
        identity_score=None,
        identity_message=(
            "分镜出图已绑定锁定身份参考图" if identity_refs else None
        ),
        identity_reference_asset_ids_json=canonical_json(identity_refs),
        identity_review_decision=None,
        identity_review_issues_json="[]",
        identity_review_note=None,
        identity_review_actor=None,
        identity_reviewed_at=None,
        identity_review_look_version=None,
        created_at=now,
    )
    session.add(take)
    trace_generation(asset, take, reused=False)
    shot.current_take = next_version
    shot.current_take_id = take.id
    shot.status = "READY"
    shot.lock_version += 1
    spec.status = "READY"
    node = session.scalar(select(WorkflowNode).where(WorkflowNode.job_id == job.id))
    if node is not None:
        node.status = "SUCCEEDED"
        node.output_json = canonical_json({"take_id": take.id, "asset_id": asset.id})
        node.updated_at = now
    session.flush()
    next_job = _enqueue_animatic_when_ready(
        session,
        job=job,
        storyboard=storyboard,
        now=now,
    )
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="storyboard.take_ready",
        payload={"shot_spec_id": spec.id, "take_id": take.id},
    )
    session.flush()
    return asset, take, next_job


def _current_storyboard_take_fingerprint(
    session: Session, storyboard: StoryboardVersion
) -> str:
    specs = list(
        session.scalars(
            select(ShotSpec)
            .where(ShotSpec.storyboard_version_id == storyboard.id)
            .order_by(ShotSpec.ordinal)
        ).all()
    )
    asset_ids: list[str] = []
    for spec in specs:
        take = session.scalar(
            select(Take).where(
                Take.shot_id == spec.shot_id,
                Take.kind == "STORYBOARD",
                Take.is_current.is_(True),
            )
        )
        if take is not None:
            asset_ids.append(take.asset_id)
    return content_hash(asset_ids)[:16]


def _enqueue_animatic_when_ready(
    session: Session,
    *,
    job: Job,
    storyboard: StoryboardVersion,
    now: datetime,
) -> Job | None:
    remaining = session.scalar(
        select(func.count(ShotSpec.id)).where(
            ShotSpec.storyboard_version_id == storyboard.id,
            ShotSpec.status != "READY",
        )
    )
    if remaining != 0:
        return None
    fingerprint = _current_storyboard_take_fingerprint(session, storyboard)
    next_job, _ = enqueue_job(
        session,
        project_id=job.project_id,
        job_type="GENERATE_ANIMATIC",
        entity_type="storyboard_version",
        entity_id=storyboard.id,
        idempotency_key=f"{job.project_id}:GENERATE_ANIMATIC:{storyboard.id}:{fingerprint}",
        input_payload={
            "storyboard_version_id": storyboard.id,
            "workflow_run_id": storyboard.workflow_run_id,
            "temporary_audio": True,
            "take_fingerprint": fingerprint,
        },
        label="分镜 · 临时声音节奏样片",
        stage="等待生成带临时对白与音乐的节奏样片",
        trace_id=job.trace_id,
        estimated_seconds=12,
        retryable=True,
    )
    storyboard.status = "ANIMATIC_RUNNING"
    storyboard.animatic_asset_id = None
    if storyboard.workflow_run_id:
        session.add(
            WorkflowNode(
                id=str(uuid4()),
                workflow_run_id=storyboard.workflow_run_id,
                node_key=f"animatic.render.{fingerprint}",
                node_type="FAN_IN",
                entity_type="storyboard_version",
                entity_id=storyboard.id,
                job_id=next_job.id,
                status="READY",
                dependency_keys_json=canonical_json([]),
                output_json="{}",
                degraded=False,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
        )
    return next_job


def regenerate_storyboard_shot(
    session: Session,
    *,
    shot_spec_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
    note: str | None = None,
) -> tuple[dict[str, object], JobRead, bool]:
    spec = session.get(ShotSpec, shot_spec_id)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜镜头不存在"},
        )
    storyboard = session.get(StoryboardVersion, spec.storyboard_version_id)
    shot = session.get(Shot, spec.shot_id)
    if storyboard is None or shot is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜版本或镜头不存在"},
        )
    project = project_or_404(session, storyboard.project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if storyboard.status == "APPROVED" or project.status == "STORYBOARD_APPROVED":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STORYBOARD_LOCKED",
                "message": "第 4 阶段已批准，无法再重生成分镜",
            },
        )
    if storyboard.status not in {"READY_FOR_REVIEW", "ANIMATIC_RUNNING", "TAKES_RUNNING"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STORYBOARD_NOT_EDITABLE",
                "message": f"当前分镜状态「{storyboard.status}」不可重生成",
            },
        )
    active = session.scalar(
        select(Job)
        .where(
            Job.job_type == "GENERATE_STORYBOARD_TAKE",
            Job.entity_id == spec.id,
            Job.status.in_({"PENDING", "RETRY_WAIT", "RUNNING", "CANCEL_REQUESTED"}),
        )
        .order_by(Job.created_at.desc())
    )
    if active is not None:
        return (
            {
                "shot_spec_id": spec.id,
                "shot_id": shot.id,
                "code": shot.code,
                "status": spec.status,
            },
            job_to_read(active),
            True,
        )
    now = datetime.now(UTC)
    revision = (spec.content_hash[:8] if spec.content_hash else "regen") + now.strftime("%H%M%S")
    seed = int(content_hash(f"{spec.id}:{revision}:{note or ''}")[:8], 16) & 0x7FFFFFFF
    characters: list[Character] = []
    try:
        character_ids = json.loads(shot.character_ids_json or "[]")
    except json.JSONDecodeError:
        character_ids = []
    if isinstance(character_ids, list) and character_ids:
        ordered = [item for item in character_ids if isinstance(item, str)]
        by_id = {
            item.id: item
            for item in session.scalars(
                select(Character).where(Character.id.in_(ordered))
            ).all()
        }
        characters = [by_id[item_id] for item_id in ordered if item_id in by_id]
    reference_asset_ids: list[str] = []
    for character in characters:
        for asset_id in _character_reference_asset_ids(session, character):
            if asset_id not in reference_asset_ids:
                reference_asset_ids.append(asset_id)
    image_prompt = build_storyboard_take_prompt(
        project,
        description=shot.description,
        dialogue=shot.dialogue,
        location=shot.location,
        time_of_day=shot.time_of_day,
        shot_size=shot.shot_size,
        camera_movement=shot.camera_movement,
        characters=characters,
        aspect_ratio=project.aspect_ratio,
    )
    cleaned_note = note.strip() if isinstance(note, str) and note.strip() else None
    if cleaned_note:
        image_prompt = f"{image_prompt}\n导演修改意图：{cleaned_note}。"
    spec.status = "QUEUED"
    shot.status = "QUEUED"
    shot.lock_version += 1
    storyboard.status = "TAKES_RUNNING"
    storyboard.animatic_asset_id = None
    project.status = "STORYBOARDING"
    project.lock_version += 1
    project.updated_at = now
    child, replayed = enqueue_job(
        session,
        project_id=project.id,
        job_type="GENERATE_STORYBOARD_TAKE",
        entity_type="shot_spec",
        entity_id=spec.id,
        idempotency_key=f"{project.id}:GENERATE_STORYBOARD_TAKE:{spec.id}:regen:{revision}",
        input_payload={
            "storyboard_version_id": storyboard.id,
            "workflow_run_id": storyboard.workflow_run_id,
            "shot_spec_id": spec.id,
            "shot_id": shot.id,
            "prompt": image_prompt,
            "reference_asset_ids": reference_asset_ids,
            "seed": seed,
            "replace_existing": True,
            "note": cleaned_note,
            "requested_by": actor,
        },
        label=f"{shot.code} · 重生成分镜",
        stage="等待按修改意图重绘分镜",
        trace_id=trace_id,
        estimated_seconds=30,
        retryable=True,
    )
    if storyboard.workflow_run_id and not replayed:
        session.add(
            WorkflowNode(
                id=str(uuid4()),
                workflow_run_id=storyboard.workflow_run_id,
                node_key=f"storyboard.take.{spec.ordinal}.regen.{revision}",
                node_type="JOB",
                entity_type="shot_spec",
                entity_id=spec.id,
                job_id=child.id,
                status="READY",
                dependency_keys_json=canonical_json([]),
                output_json="{}",
                degraded=False,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
        )
    append_event(
        session,
        project_id=project.id,
        job_id=child.id,
        event_type="storyboard.take_regenerate_requested",
        payload={
            "shot_spec_id": spec.id,
            "shot_id": shot.id,
            "code": shot.code,
            "note": cleaned_note,
            "actor": actor,
        },
    )
    session.commit()
    session.refresh(child)
    return (
        {
            "shot_spec_id": spec.id,
            "shot_id": shot.id,
            "code": shot.code,
            "status": spec.status,
        },
        job_to_read(child),
        replayed,
    )



def revert_failed_storyboard_take(session: Session, job: Job) -> None:
    """分镜重生成最终失败时回滚镜头状态，避免永久卡在「生成中」。"""
    try:
        payload = json.loads(job.input_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    spec_id = payload.get("shot_spec_id") or job.entity_id
    spec = session.get(ShotSpec, str(spec_id)) if spec_id else None
    shot_id = payload.get("shot_id") or (spec.shot_id if spec is not None else None)
    shot = session.get(Shot, str(shot_id)) if shot_id else None
    if spec is None or shot is None:
        return
    current_take = session.scalar(
        select(Take).where(
            Take.shot_id == shot.id,
            Take.kind == "STORYBOARD",
            Take.is_current.is_(True),
        )
    )
    if current_take is not None:
        spec.status = "READY"
        shot.status = "READY"
    else:
        spec.status = "FAILED"
        shot.status = "FAILED"
    shot.lock_version += 1

    storyboard_id = payload.get("storyboard_version_id") or spec.storyboard_version_id
    storyboard = session.get(StoryboardVersion, str(storyboard_id)) if storyboard_id else None
    if storyboard is None:
        return
    remaining = session.scalar(
        select(func.count(ShotSpec.id)).where(
            ShotSpec.storyboard_version_id == storyboard.id,
            ShotSpec.status.notin_(["READY", "FAILED"]),
        )
    )
    if remaining != 0:
        return
    storyboard.status = "READY_FOR_REVIEW"
    project = session.get(Project, job.project_id)
    if project is not None and project.status in {"STORYBOARDING", "STORYBOARD_READY"}:
        project.status = "STORYBOARD_READY"
        project.lock_version += 1
        project.updated_at = datetime.now(UTC)


def heal_stale_storyboard_take_queue(session: Session, storyboard: StoryboardVersion) -> bool:
    """读取工作区时自愈：QUEUED 但已无活跃任务的镜头恢复为 READY/FAILED。"""
    specs = list(
        session.scalars(
            select(ShotSpec).where(
                ShotSpec.storyboard_version_id == storyboard.id,
                ShotSpec.status == "QUEUED",
            )
        ).all()
    )
    if not specs:
        return False
    changed = False
    for spec in specs:
        active = session.scalar(
            select(Job.id).where(
                Job.job_type == "GENERATE_STORYBOARD_TAKE",
                Job.entity_id == spec.id,
                Job.status.in_({"PENDING", "RETRY_WAIT", "RUNNING", "CANCEL_REQUESTED"}),
            )
        )
        if active is not None:
            continue
        shot = session.get(Shot, spec.shot_id)
        if shot is None:
            continue
        current_take = session.scalar(
            select(Take).where(
                Take.shot_id == shot.id,
                Take.kind == "STORYBOARD",
                Take.is_current.is_(True),
            )
        )
        if current_take is not None:
            spec.status = "READY"
            shot.status = "READY"
        else:
            spec.status = "FAILED"
            shot.status = "FAILED"
        shot.lock_version += 1
        changed = True
    if not changed:
        return False
    remaining = session.scalar(
        select(func.count(ShotSpec.id)).where(
            ShotSpec.storyboard_version_id == storyboard.id,
            ShotSpec.status.notin_(["READY", "FAILED"]),
        )
    )
    if remaining == 0 and storyboard.status in {"TAKES_RUNNING", "ANIMATIC_RUNNING"}:
        storyboard.status = "READY_FOR_REVIEW"
        project = session.get(Project, storyboard.project_id)
        if project is not None and project.status == "STORYBOARDING":
            project.status = "STORYBOARD_READY"
            project.lock_version += 1
            project.updated_at = datetime.now(UTC)
    session.commit()
    return True


def animatic_inputs(
    session: Session,
    settings: Settings,
    job: Job,
) -> tuple[Project, StoryboardVersion, list[PreviewShot]]:
    payload = json.loads(job.input_json)
    storyboard = session.get(StoryboardVersion, str(payload["storyboard_version_id"]))
    if storyboard is None:
        raise ValueError("分镜版本不存在")
    project = project_or_404(session, job.project_id)
    specs = list(
        session.scalars(
            select(ShotSpec)
            .where(ShotSpec.storyboard_version_id == storyboard.id)
            .order_by(ShotSpec.ordinal)
        ).all()
    )
    shots: list[PreviewShot] = []
    for spec in specs:
        shot = session.get(Shot, spec.shot_id)
        take = session.scalar(
            select(Take).where(
                Take.shot_id == spec.shot_id,
                Take.kind == "STORYBOARD",
                Take.is_current.is_(True),
            )
        )
        if shot is None or take is None:
            raise ValueError("节奏样片缺少分镜版本")
        asset = session.get(Asset, take.asset_id)
        if asset is None:
            raise ValueError("分镜资产不存在")
        shots.append(
            PreviewShot(
                id=shot.id,
                code=shot.code,
                title=shot.title,
                dialogue=shot.dialogue,
                duration_sec=shot.duration_sec,
                image_path=resolve_asset_path(settings, asset),
            )
        )
    return project, storyboard, shots


def register_animatic(
    session: Session,
    settings: Settings,
    *,
    job: Job,
    storyboard: StoryboardVersion,
    files: PreviewFiles,
) -> Asset:
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind="animatic",
        source=files.mp4,
        source_entity_type="storyboard_version",
        source_entity_id=storyboard.id,
        mime="video/mp4",
        width=files.width,
        height=files.height,
        duration_ms=files.duration_ms,
    )
    asset.metadata_json = canonical_json(
        {
            "storyboard_version_id": storyboard.id,
            "temporary_dialogue": True,
            "temporary_music": True,
            "probe": files.probe,
        }
    )
    for kind, source, mime in (
        ("animatic_srt", files.srt, "application/x-subrip"),
        ("animatic_vtt", files.vtt, "text/vtt"),
        ("animatic_manifest", files.manifest, "application/json"),
    ):
        register_file(
            session,
            settings,
            project_id=job.project_id,
            kind=kind,
            source=source,
            source_entity_type="storyboard_version",
            source_entity_id=storyboard.id,
            mime=mime,
        )
    now = datetime.now(UTC)
    storyboard.animatic_asset_id = asset.id
    storyboard.status = "READY_FOR_REVIEW"
    project = project_or_404(session, job.project_id)
    project.status = "STORYBOARD_READY"
    project.lock_version += 1
    project.updated_at = now
    if storyboard.workflow_run_id:
        workflow = session.get(WorkflowRun, storyboard.workflow_run_id)
        if workflow is not None:
            workflow.status = "WAITING_FOR_GATE"
            workflow.current_gate = "G4_STORYBOARD"
            workflow.updated_at = now
        node = session.scalar(select(WorkflowNode).where(WorkflowNode.job_id == job.id))
        if node is not None:
            node.status = "SUCCEEDED"
            node.output_json = canonical_json({"animatic_asset_id": asset.id})
            node.updated_at = now
        existing_gate = session.scalar(
            select(ReviewGate).where(
                ReviewGate.workflow_run_id == storyboard.workflow_run_id,
                ReviewGate.gate_key == "G4_STORYBOARD",
            )
        )
        if existing_gate is None:
            session.add(
                ReviewGate(
                    id=str(uuid4()),
                    workflow_run_id=storyboard.workflow_run_id,
                    project_id=job.project_id,
                    gate_key="G4_STORYBOARD",
                    entity_type="storyboard_version",
                    entity_id=storyboard.id,
                    status="PENDING_REVIEW",
                    decision=None,
                    decided_by=None,
                    decided_at=None,
                    created_at=now,
                )
            )
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="animatic.ready",
        payload={"storyboard_version_id": storyboard.id, "asset_id": asset.id},
    )
    session.flush()
    return asset


def storyboard_workspace(session: Session, project_id: str) -> dict[str, object]:
    project_or_404(session, project_id)
    storyboard = session.scalar(
        select(StoryboardVersion)
        .where(StoryboardVersion.project_id == project_id)
        .order_by(StoryboardVersion.version.desc())
    )
    if storyboard is None:
        return {"storyboard": None, "shots": [], "workflow": None, "gate": None}
    heal_stale_storyboard_take_queue(session, storyboard)
    session.refresh(storyboard)
    specs = list(
        session.scalars(
            select(ShotSpec)
            .where(ShotSpec.storyboard_version_id == storyboard.id)
            .order_by(ShotSpec.ordinal)
        ).all()
    )
    shots = []
    for spec in specs:
        shot = session.get(Shot, spec.shot_id)
        take = session.scalar(
            select(Take).where(
                Take.shot_id == spec.shot_id,
                Take.kind == "STORYBOARD",
                Take.is_current.is_(True),
            )
        )
        shots.append(
            {
                "shot_spec_id": spec.id,
                "shot_id": spec.shot_id,
                "code": shot.code if shot else f"S{spec.ordinal:02d}",
                "title": shot.title if shot else "",
                "description": spec.description,
                "dialogue": spec.dialogue,
                "duration_ms": spec.duration_ms,
                "shot_size": spec.shot_size,
                "camera_movement": spec.camera_movement,
                "character_look_ids": json.loads(spec.character_look_ids_json),
                "location_version_id": spec.location_version_id,
                "prop_version_ids": json.loads(spec.prop_version_ids_json),
                "status": spec.status,
                "image_url": f"/api/v1/assets/{take.asset_id}/content" if take else None,
                "content_hash": spec.content_hash,
            }
        )
    workflow = (
        session.get(WorkflowRun, storyboard.workflow_run_id) if storyboard.workflow_run_id else None
    )
    nodes = (
        list(
            session.scalars(
                select(WorkflowNode)
                .where(WorkflowNode.workflow_run_id == workflow.id)
                .order_by(WorkflowNode.created_at)
            ).all()
        )
        if workflow
        else []
    )
    gate = (
        session.scalar(
            select(ReviewGate).where(
                ReviewGate.workflow_run_id == workflow.id,
                ReviewGate.gate_key == "G4_STORYBOARD",
            )
        )
        if workflow
        else None
    )
    return {
        "storyboard": {
            "id": storyboard.id,
            "version": storyboard.version,
            "status": storyboard.status,
            "episode_id": storyboard.episode_id,
            "script_version_id": storyboard.script_version_id,
            "visual_bible_version_id": storyboard.visual_bible_version_id,
            "content_hash": storyboard.content_hash,
            "animatic_url": (
                f"/api/v1/assets/{storyboard.animatic_asset_id}/content"
                if storyboard.animatic_asset_id
                else None
            ),
        },
        "shots": shots,
        "workflow": (
            {
                "id": workflow.id,
                "status": workflow.status,
                "current_gate": workflow.current_gate,
                "nodes": [
                    {
                        "id": item.id,
                        "node_key": item.node_key,
                        "node_type": item.node_type,
                        "status": item.status,
                        "job_id": item.job_id,
                        "dependencies": json.loads(item.dependency_keys_json),
                        "degraded": item.degraded,
                    }
                    for item in nodes
                ],
            }
            if workflow
            else None
        ),
        "gate": (
            {
                "id": gate.id,
                "gate_key": gate.gate_key,
                "status": gate.status,
                "decision": gate.decision,
            }
            if gate
            else None
        ),
    }


def approve_storyboard(
    session: Session,
    *,
    storyboard_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
    commit: bool = True,
) -> tuple[dict[str, object], JobRead, bool]:
    storyboard = session.get(StoryboardVersion, storyboard_id)
    if storyboard is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜版本不存在"},
        )
    project = project_or_404(session, storyboard.project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "STORYBOARD_READY" or storyboard.status != "READY_FOR_REVIEW":
        raise HTTPException(
            status_code=409,
            detail={"code": "STORYBOARD_NOT_READY", "message": "分镜尚未达到批准条件"},
        )
    now = datetime.now(UTC)
    storyboard.status = "APPROVED"
    storyboard.approved_at = now
    storyboard.approved_by = actor
    project.status = "STORYBOARD_APPROVED"
    project.lock_version += 1
    project.updated_at = now
    workflow = session.get(WorkflowRun, storyboard.workflow_run_id)
    if workflow is not None:
        workflow.status = "RUNNING"
        workflow.current_gate = None
        workflow.updated_at = now
    gate = session.scalar(
        select(ReviewGate).where(
            ReviewGate.workflow_run_id == storyboard.workflow_run_id,
            ReviewGate.gate_key == "G4_STORYBOARD",
        )
    )
    if gate is not None:
        gate.status = "APPROVED"
        gate.decision = "APPROVE"
        gate.decided_by = actor
        gate.decided_at = now
    job, replayed = enqueue_job(
        session,
        project_id=project.id,
        job_type="START_MEDIA_PRODUCTION",
        entity_type="storyboard_version",
        entity_id=storyboard.id,
        idempotency_key=f"{project.id}:START_MEDIA_PRODUCTION:{storyboard.id}:v1",
        input_payload={
            "storyboard_version_id": storyboard.id,
            "workflow_run_id": storyboard.workflow_run_id,
            "config_version": "media-production-v1",
        },
        label=f"{project.name} · 正式媒体生产",
        stage="等待展开正式关键帧、视频与音频任务",
        trace_id=trace_id,
        estimated_seconds=4,
        retryable=True,
    )
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="storyboard.approved",
        payload={"storyboard_version_id": storyboard.id},
    )
    session.flush()
    if commit:
        session.commit()
    session.refresh(job)
    return (
        {
            "id": storyboard.id,
            "version": storyboard.version,
            "status": storyboard.status,
        },
        job_to_read(job),
        replayed,
    )


def list_workflow_runs(session: Session, project_id: str) -> list[dict[str, object]]:
    project_or_404(session, project_id)
    runs = session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == project_id)
        .order_by(WorkflowRun.created_at.desc())
    ).all()
    result = []
    for run in runs:
        nodes = session.scalars(
            select(WorkflowNode)
            .where(WorkflowNode.workflow_run_id == run.id)
            .order_by(WorkflowNode.created_at)
        ).all()
        result.append(
            {
                "id": run.id,
                "workflow_type": run.workflow_type,
                "status": run.status,
                "current_gate": run.current_gate,
                "source_entity_type": run.source_entity_type,
                "source_entity_id": run.source_entity_id,
                "nodes": [
                    {
                        "id": item.id,
                        "node_key": item.node_key,
                        "node_type": item.node_type,
                        "entity_type": item.entity_type,
                        "entity_id": item.entity_id,
                        "job_id": item.job_id,
                        "status": item.status,
                        "dependencies": json.loads(item.dependency_keys_json),
                        "degraded": item.degraded,
                        "error_code": item.error_code,
                    }
                    for item in nodes
                ],
            }
        )
    return result
