import base64
import json
import re
from dataclasses import dataclass
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
from app.domain.shot_spec import ShotSpec as StructuredShotSpec
from app.schemas import JobRead
from app.services.assets import register_file, resolve_asset_path
from app.services.character_image_qc import detect_lower_right_watermark
from app.services.director_intent_consumption import (
    confirmed_director_intents_by_scene,
    director_intent_prompt_block,
    update_director_intent_inheritance_receipt,
)
from app.services.events import append_event
from app.services.generation_records import ensure_generation_record
from app.services.image_provider import GeneratedImage
from app.services.jobs import enqueue_job, job_to_read
from app.services.media import PreviewFiles, PreviewShot
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.provenance import record_shot_spec_revision
from app.services.shot_frames import (
    RENDER_MODE_BLACK_FRAME,
    VISIBILITY_BACK_ONLY,
    VISIBILITY_FACE_VISIBLE,
    VISIBILITY_HANDS_ONLY,
    VISIBILITY_OFF_SCREEN,
    FrameBeat,
    FrameConflict,
    PromptDebug,
    PromptDebugRemoval,
    blocking_conflicts,
    collapse_to_single_beat,
    compile_static_frame_brief,
    detect_frame_conflicts,
    resolve_visibility,
    split_visual_moments,
    visibility_prompt_clause,
)
from app.services.shot_specs import (
    build_structured_shot_spec,
    compile_shot_spec,
    ensure_shot_spec_generation_ready,
    load_shot_spec_contract,
    write_shot_spec,
)
from app.services.workspace import project_or_404

# 兼容旧测试与调用方命名
compile_single_frame_visual_brief = compile_static_frame_brief


def _json_object(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


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


# 集体称谓 → 具名角色名（剧本写「夫妻」时应对齐锁定的妻子/丈夫）
_COLLECTIVE_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "夫妻": ("妻子", "丈夫"),
    "夫妇": ("妻子", "丈夫"),
    "两口子": ("妻子", "丈夫"),
}
_GLOVE_HAND_TOKENS = ("无菌白手套", "无菌手套", "白手套", "戴手套的手", "戴着白色手套")


def _mentioned_character_keys(
    text: str,
    characters_by_key: dict[str, Character],
) -> list[str]:
    """从画面文案提取出镜角色；支持夫妻等集体称谓与隐面手套手。"""
    if not text:
        return []
    mentioned: list[str] = []
    names_to_keys: dict[str, list[str]] = {}
    for character_key, character in characters_by_key.items():
        names_to_keys.setdefault(character.name, []).append(character_key)
        if character.name in text or character_key in text:
            mentioned.append(character_key)

    for alias, member_names in _COLLECTIVE_NAME_ALIASES.items():
        if alias not in text:
            continue
        for name in member_names:
            for character_key in names_to_keys.get(name, []):
                if character_key not in mentioned:
                    mentioned.append(character_key)
        # 名称非「妻子/丈夫」但 key 标明配偶时也纳入
        for character_key in characters_by_key:
            if character_key in mentioned:
                continue
            key_lower = character_key.lower()
            if key_lower.startswith("spouse_") or "wife" in key_lower or "husband" in key_lower:
                mentioned.append(character_key)

    if any(token in text for token in _GLOVE_HAND_TOKENS):
        for character_key, character in characters_by_key.items():
            brief = character.visual_brief or ""
            if character_key in mentioned:
                continue
            if _is_face_hidden_brief(brief) and "手套" in brief:
                mentioned.append(character_key)
    return mentioned


def _scene_character_keys(
    lines: list[ScriptLine],
    characters_by_key: dict[str, Character],
) -> list[str]:
    """场次可出镜角色：只认 ACTION 画面提名与露脸对白说话人，不含 VO 台词点名。"""
    result: list[str] = []
    for line in lines:
        if line.line_type == "ACTION":
            candidates = [
                line.speaker_key,
                *_mentioned_character_keys(line.text, characters_by_key),
            ]
        elif line.line_type == "DIALOGUE":
            speaker = characters_by_key.get(line.speaker_key)
            if speaker is not None and _is_face_hidden_brief(speaker.visual_brief):
                candidates = []
            else:
                candidates = [line.speaker_key]
        else:
            candidates = []
        for character_key in candidates:
            if character_key in characters_by_key and character_key not in result:
                result.append(character_key)
    return result[:8]


def _characters_from_visual_text(
    text: str,
    characters_by_key: dict[str, Character],
) -> list[Character]:
    """按画面描述解析应锁定身份的出镜角色。"""
    return [
        characters_by_key[key]
        for key in _mentioned_character_keys(text, characters_by_key)
        if key in characters_by_key
    ]


def _load_project_characters_by_key(
    session: Session,
    project_id: str,
) -> dict[str, Character]:
    return {
        character.character_key: character
        for character in session.scalars(
            select(Character).where(Character.project_id == project_id)
        ).all()
    }


def _resolve_bound_characters_for_shot(
    session: Session,
    *,
    project_id: str,
    shot: Shot,
    delivery: str,
) -> list[Character]:
    """出图时按画面描述重绑角色，愈合「夫妻」未映射导致的错误角色表。"""
    characters_by_key = _load_project_characters_by_key(session, project_id)
    if not characters_by_key:
        return []
    inferred = _characters_from_visual_text(shot.description or "", characters_by_key)
    if inferred:
        return inferred[:8]
    # 描述无法提名时回退到镜头已存绑定
    try:
        stored_ids = json.loads(shot.character_ids_json or "[]")
    except json.JSONDecodeError:
        stored_ids = []
    if not isinstance(stored_ids, list):
        stored_ids = []
    by_id = {character.id: character for character in characters_by_key.values()}
    ordered = [item for item in stored_ids if isinstance(item, str) and item in by_id]
    _ = delivery  # 描述优先；无法提名时回退存量绑定
    return [by_id[item_id] for item_id in ordered][:8]


@dataclass(frozen=True)
class FrameBinding:
    """当前画面实际需要的角色、可见范围与参考资产。"""

    characters: list[Character]
    visibility_by_name: dict[str, str]
    absent_names: tuple[str, ...]
    reference_asset_ids: list[str]
    conflicts: list[FrameConflict]


def resolve_frame_bindings(
    session: Session,
    *,
    frame: FrameBeat,
    delivery: str,
    shot_size: str,
    camera_movement: str,
    frame_named: list[Character],
    fallback: list[Character] = (),
    scene_cast: list[Character] = (),
    offscreen_names: tuple[str, ...] = (),
    face_hidden_names: tuple[str, ...] = (),
    location: LocationVersion | None = None,
    props: list[PropVersion] | None = None,
) -> FrameBinding:
    """按「画面实际可见区域」选出镜角色与参考资产，并检查生图冲突。"""
    declared_offscreen = {
        character.name
        for character in (*frame_named, *fallback, *scene_cast)
        if any(character.name and character.name in item for item in offscreen_names)
    }
    # 画面点名的角色优先；画外音镜头默认无人出镜；回退角色也必须在本帧有可见证据
    candidates = [item for item in frame_named if item.name not in declared_offscreen]
    if not candidates and delivery != "VOICE_OVER":
        candidates = [
            item
            for item in fallback
            if item.name not in declared_offscreen
            and _frame_evidences_character(item, frame.visual)
        ]

    visibility_by_name: dict[str, str] = {}
    on_camera: list[Character] = []
    for character in candidates:
        visibility = resolve_visibility(
            character_name=character.name,
            visual_brief=character.visual_brief,
            frame_text=frame.visual,
            delivery=delivery,
            offscreen_names=offscreen_names,
            face_hidden_names=face_hidden_names,
        )
        if visibility == VISIBILITY_OFF_SCREEN:
            continue
        visibility_by_name[character.name] = visibility
        on_camera.append(character)

    on_camera_names = {character.name for character in on_camera}
    absent = [
        character.name
        for character in (*scene_cast, *fallback, *frame_named)
        if character.name not in on_camera_names
    ]
    absent = list(dict.fromkeys(absent))

    reference_asset_ids: list[str] = []
    for character in on_camera:
        for asset_id in _character_reference_asset_ids(
            session,
            character,
            visibility=visibility_by_name[character.name],
        ):
            if asset_id not in reference_asset_ids:
                reference_asset_ids.append(asset_id)
    identity_reference_count = len(reference_asset_ids)
    for asset_id in _scene_reference_asset_ids(frame.visual, location=location, props=props or []):
        if asset_id not in reference_asset_ids:
            reference_asset_ids.append(asset_id)

    conflicts = detect_frame_conflicts(
        frame_text=frame.visual,
        shot_size=shot_size,
        camera_movement=camera_movement,
        visible_character_count=len(on_camera),
        face_visible_count=sum(
            1 for value in visibility_by_name.values() if value == VISIBILITY_FACE_VISIBLE
        ),
        identity_reference_count=identity_reference_count,
    )
    return FrameBinding(
        characters=on_camera,
        visibility_by_name=visibility_by_name,
        absent_names=tuple(absent),
        reference_asset_ids=reference_asset_ids[:8],
        conflicts=conflicts,
    )


def _frame_evidences_character(character: Character, frame_text: str) -> bool:
    """回退绑定时：本帧必须有该角色的可见证据，避免空镜灌入整场 cast。"""
    text = frame_text or ""
    if character.name and character.name in text:
        return True
    brief = character.visual_brief or ""
    glove_tokens = ("手套", "双手", "手部", "指尖", "袖口", "手腕")
    back_tokens = ("背影", "背对镜头", "背对着镜头", "从背后")
    if any(token in text for token in glove_tokens) and (
        _is_face_hidden_brief(brief) or any(token in brief for token in glove_tokens)
    ):
        return True
    if any(token in text for token in back_tokens):
        return True
    return False


def _merge_names(current: tuple[str, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*current, *extra)))


def _visible_prop_version_ids(frame_text: str, props: list[PropVersion]) -> list[str]:
    """只绑定画面里点名的道具，避免整片道具表灌进每个镜头。"""
    visible = [item.id for item in props if item.name and item.name in frame_text]
    return visible


def _frame_prompt_payload(
    *,
    beat: FrameBeat,
    binding: FrameBinding,
    project: Project,
    description: str,
    dialogue: str,
    delivery: str,
    location_name: str,
    time_of_day: str,
    shot_size: str,
    camera: str,
    location_version_id: str | None,
    prop_version_ids: list[str],
    image_prompt: str,
    offscreen_names: tuple[str, ...],
    face_hidden_names: tuple[str, ...],
    prompt_intent: dict[str, object] | None,
    storyboard_intent: dict[str, object] | None,
    identity_ids: list[str],
    look_ids: list[str],
    story_state_ids: list[str],
) -> dict[str, object]:
    """ShotSpec.prompt_json：静态出图规格 + 声音/运镜/时间轴等非画面信息分字段留存。"""
    return {
        "description": description,
        "dialogue": dialogue,
        "delivery": delivery,
        "style": project.style,
        "location": location_name,
        "time_of_day": time_of_day,
        "shot_size": shot_size,
        "camera": camera,
        "render_mode": beat.render_mode,
        # 以下三项刻意不进入生图提示词，仅供时间线、音频与剪辑阶段使用
        "audio_cues": list(beat.audio_cues),
        "camera_notes": list(beat.camera_notes),
        "timeline_notes": list(beat.timeline_notes),
        "character_ids": [character.id for character in binding.characters],
        "character_names": [character.name for character in binding.characters],
        "character_visibility": binding.visibility_by_name,
        "absent_character_names": list(binding.absent_names),
        "offscreen_names": list(offscreen_names),
        "face_hidden_names": list(face_hidden_names),
        "character_identity_version_ids": identity_ids,
        "character_look_ids": look_ids,
        "character_story_state_version_ids": story_state_ids,
        "location_version_id": location_version_id,
        "prop_version_ids": prop_version_ids,
        "reference_asset_ids": binding.reference_asset_ids,
        "image_prompt": image_prompt,
        "creative_bible": {},
        "prompt_debug": {},
        "frame_conflicts": [
            {"code": item.code, "severity": item.severity, "message": item.message}
            for item in binding.conflicts
        ],
        "director_intent": prompt_intent,
        "storyboard_director_intent": storyboard_intent,
    }


def _version_reference_asset_ids(payload: str | None) -> list[str]:
    try:
        values = json.loads(payload or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, str) and item]


def _scene_reference_asset_ids(
    frame_text: str,
    *,
    location: LocationVersion | None,
    props: list[PropVersion],
) -> list[str]:
    """场景参考图始终跟随当前地点；道具只传画面里点名的那几件。"""
    asset_ids: list[str] = []
    if location is not None:
        asset_ids.extend(_version_reference_asset_ids(location.reference_asset_ids_json)[:1])
    for prop in props:
        if prop.name and prop.name in frame_text:
            asset_ids.extend(_version_reference_asset_ids(prop.reference_asset_ids_json)[:1])
    return asset_ids


def _is_face_hidden_brief(visual_brief: str | None) -> bool:
    """角色设定要求不露脸或仅出局部（手/剪影）时视为隐面。"""
    brief = (visual_brief or "").strip()
    if not brief:
        return False
    tokens = (
        "不露出面部",
        "不露脸",
        "全程不露出面部",
        "仅出现",
        "只出手",
        "仅出手",
        "戴无菌",
        "无菌白手套",
        "不露出可识别",
    )
    return any(token in brief for token in tokens)


def _storyboard_static_frame_direction(
    *,
    shot_size: str,
    time_of_day: str,
    has_on_camera_dialogue: bool,
    has_visible_cast: bool,
    is_macro: bool,
) -> str:
    """单帧静帧摄影指引：只有景别与光线，不含运镜过程。"""
    shot_language = {
        "WS": "广角全景静帧，环境纵深可见，主体不必居中",
        "MS": "中景静帧，人物与环境信息平衡",
        "MCU": "中近景静帧，上半身与表情主导画面",
        "CU": (
            "100mm macro 极微距静帧，焦点落在物件表面材质与凝结细节"
            if is_macro
            else "近景静帧，焦点在局部材质或物件表面"
        ),
    }.get(shot_size, f"{shot_size} 静帧景别")
    time_lower = time_of_day.lower()
    if any(token in time_lower for token in ("夜", "night", "晚", "凌晨", "午夜")):
        lighting = "冷色实景光与局部暖光并存，主体有明确明暗交界，拒绝平光美颜"
    else:
        lighting = "侧前方自然主光塑造体积，保留真实阴影与材质反光"
    # 无人可见时不下发任何人像表演与皮肤要求，避免诱导模型添人
    if not has_visible_cast:
        texture = "材质保留真实磨损、灰尘与冷凝痕迹"
        performance = "画面中不出现任何人物、人脸、手部或身体局部"
    else:
        texture = "皮肤保留毛孔与细微瑕疵，衣料有真实褶皱"
        performance = (
            "表情克制，口部可呈说话瞬间，禁止摆拍假笑"
            if has_on_camera_dialogue
            else "表情克制自然，禁止空眼神与塑料微笑"
        )
    return (
        f"{shot_language}。固定机位单帧，禁止表现推拉摇移过程。"
        f"{performance}。{lighting}。"
        f"按电影剧照/实拍静帧理解：非对称构图、空气透视与生活痕迹；{texture}。"
        "严禁：居中证件照、磨皮美颜、塑料皮肤、文字字幕水印、拼贴分镜格。"
    )


def _shot_delivery(
    line: ScriptLine,
    speaker: Character | None,
) -> str:
    """将剧本行映射为生图交付方式：ACTION / VOICE_OVER / DIALOGUE。"""
    if line.line_type == "ACTION":
        return "ACTION"
    if line.line_type == "VOICE_OVER":
        return "VOICE_OVER"
    if line.line_type == "DIALOGUE":
        if speaker is not None and _is_face_hidden_brief(speaker.visual_brief):
            return "VOICE_OVER"
        return "DIALOGUE"
    # 未知类型按画外音保守处理，避免误生成口型镜头
    return "VOICE_OVER"


def _action_visual_text(purpose: str, action_line: ScriptLine | None) -> str:
    if action_line is None:
        return purpose.strip()
    purpose_part = purpose.strip()
    action_part = action_line.text.strip()
    if purpose_part and action_part:
        return f"{purpose_part}。{action_part}"
    return purpose_part or action_part


def _visual_description_for_line(
    line: ScriptLine,
    *,
    delivery: str,
    purpose: str,
    speaking_label: str,
    action_visual: str,
) -> str:
    """按交付方式组装镜头画面描述，VO 继承同场 ACTION 视觉。"""
    action_visual = action_visual.strip() or purpose.strip() or "延续当前场景空间"
    if delivery == "ACTION":
        return _action_visual_text(purpose, line)
    if delivery == "VOICE_OVER":
        return f"画外音覆盖于：{action_visual}"
    # DIALOGUE：保留表演意图，同时锚定同场视觉，避免丢分镜画面
    performance = (
        f"{speaking_label}以{line.emotion}状态完成台词，保持与锁定身份参考图为同一人"
    )
    if action_visual and action_visual != purpose.strip():
        return f"{performance}。视觉锚点：{action_visual}"
    return performance


def _line_character_keys(
    line: ScriptLine,
    *,
    characters_by_key: dict[str, Character],
    scene_character_keys: list[str],
    delivery: str | None = None,
    visual_anchor_text: str | None = None,
) -> list[str]:
    """按交付方式绑定出镜角色；台词提名不再自动入镜。"""
    resolved_delivery = delivery or (
        "ACTION"
        if line.line_type == "ACTION"
        else "VOICE_OVER"
        if line.line_type == "VOICE_OVER"
        else "DIALOGUE"
    )
    result: list[str] = []
    if resolved_delivery == "ACTION":
        candidates = [line.speaker_key, *_mentioned_character_keys(line.text, characters_by_key)]
        for character_key in candidates:
            if character_key in characters_by_key and character_key not in result:
                result.append(character_key)
        if not result:
            result.extend(scene_character_keys)
        return result[:8]

    # VO / 隐面台词：只认画面锚点里点名的角色，不因 speaker 或台词提及入镜
    anchor = visual_anchor_text or ""
    for character_key in _mentioned_character_keys(anchor, characters_by_key):
        if character_key not in result:
            result.append(character_key)
    if resolved_delivery == "DIALOGUE" and line.speaker_key in characters_by_key:
        if line.speaker_key not in result:
            result.insert(0, line.speaker_key)
    return result[:8]


_IDENTITY_VIEW_PRIORITY = ("FRONT", "THREE_QUARTER", "FULL_BODY", "PROFILE")


def _look_reference_asset_ids(session: Session, character: Character) -> list[str]:
    """造型参考图：服装、手套、袖口等可见着装依据。"""
    if not character.active_look_version_id:
        return []
    look = session.get(CharacterLookVersion, character.active_look_version_id)
    if look is None:
        return []
    try:
        look_refs = json.loads(look.reference_asset_ids_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(look_refs, list):
        return []
    return [item for item in look_refs if isinstance(item, str) and item]


def _identity_assets_by_view(session: Session, character: Character) -> dict[str, str]:
    if not character.locked_identity_version_id:
        return {}
    identity_assets = session.scalars(
        select(CharacterIdentityAsset).where(
            CharacterIdentityAsset.identity_version_id
            == character.locked_identity_version_id
        )
    ).all()
    return {
        item.view_type: item.asset_id
        for item in identity_assets
        if item.asset_id and item.view_type != "EXPRESSIONS"
    }


def _character_reference_asset_ids(
    session: Session,
    character: Character,
    *,
    visibility: str = VISIBILITY_FACE_VISIBLE,
) -> list[str]:
    """按画面实际可见区域选参考图，避免无关参考诱导模型加人。"""
    if visibility == VISIBILITY_OFF_SCREEN:
        return []
    if visibility == VISIBILITY_HANDS_ONLY:
        # 只出现手部时用造型参考（手套、袖口），不传正脸身份图
        return _look_reference_asset_ids(session, character)[:3]

    by_view = _identity_assets_by_view(session, character)
    asset_ids: list[str] = []
    if visibility == VISIBILITY_BACK_ONLY:
        # 背影优先体态与服装轮廓，不传正脸
        for view_type in ("FULL_BODY", "PROFILE"):
            asset_id = by_view.get(view_type)
            if asset_id and asset_id not in asset_ids:
                asset_ids.append(asset_id)
    else:
        for view_type in _IDENTITY_VIEW_PRIORITY:
            asset_id = by_view.get(view_type)
            if asset_id and asset_id not in asset_ids:
                asset_ids.append(asset_id)
                break
        full_body = by_view.get("FULL_BODY")
        if full_body and full_body not in asset_ids:
            asset_ids.append(full_body)
    if not asset_ids and character.locked_candidate_id and visibility == VISIBILITY_FACE_VISIBLE:
        candidate = session.get(CharacterCandidate, character.locked_candidate_id)
        if candidate is not None and candidate.asset_id:
            asset_ids.append(candidate.asset_id)
    for asset_id in _look_reference_asset_ids(session, character):
        if asset_id not in asset_ids:
            asset_ids.append(asset_id)
    return asset_ids[:3]


def _storyboard_identity_prompt(
    characters: list[Character],
    *,
    visibility_by_name: dict[str, str],
) -> str:
    """仅输出当前画面可见角色；无人出镜时不写身份/五官/服装模板。"""
    if not characters:
        return ""

    reference_lines = [
        visibility_prompt_clause(
            character.name,
            character.role,
            character.visual_brief.strip() or "沿用锁定身份五官与发型",
            visibility_by_name.get(character.name, VISIBILITY_FACE_VISIBLE),
        )
        for character in characters
        if visibility_by_name.get(character.name, VISIBILITY_FACE_VISIBLE)
        != VISIBILITY_OFF_SCREEN
    ]
    if not reference_lines:
        return ""

    visibilities = {
        visibility_by_name.get(character.name, VISIBILITY_FACE_VISIBLE)
        for character in characters
        if visibility_by_name.get(character.name, VISIBILITY_FACE_VISIBLE)
        != VISIBILITY_OFF_SCREEN
    }
    constraints: list[str] = ["角色身份锁定（硬约束）：", *reference_lines]
    if VISIBILITY_FACE_VISIBLE in visibilities:
        constraints.extend(
            (
                "- 输入参考图是每个露脸角色唯一的身份基准。画面中的人物必须与参考图为同一人：",
                "脸型、五官比例、瞳距、鼻梁、唇形、发型核心特征、发色、年龄感与辨识度保持一致。",
            )
        )
    if visibilities & {VISIBILITY_HANDS_ONLY, VISIBILITY_BACK_ONLY}:
        constraints.append(
            "- 局部出镜角色以可见范围为准，不要求五官或唇形可见，禁止用口罩折中出完整人脸。"
        )
    constraints.extend(
        (
            "- 允许改变表情、姿势、景别、光线与背景；禁止换脸、混脸、另造相似替身。",
            "- 当前镜头未绑定的角色不要入镜；不要新增路人抢戏。",
        )
    )
    return "\n".join(constraints)


def _idea_section(idea: str, start_label: str, end_labels: tuple[str, ...]) -> str:
    """从创意正文截取命名小节段落。"""
    if not idea or not start_label:
        return ""
    match = re.search(
        rf"{re.escape(start_label)}\s*[：:：]?\s*",
        idea,
    )
    if match is None:
        return ""
    start = match.end()
    end = len(idea)
    for label in end_labels:
        next_match = re.search(rf"\n\s*{re.escape(label)}\s*[：:：]?", idea[start:])
        if next_match is not None:
            end = min(end, start + next_match.start())
    return idea[start:end].strip()


def _compact_prompt_clause(text: str, max_chars: int) -> str:
    """压缩段落空白并截断，供生图提示词使用。"""
    cleaned = re.sub(r"\s+", "", (text or "").strip())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip("，,；;、") + "…"


def _build_creative_bible(project: Project) -> dict[str, str]:
    """Global Creative Bible：只存储，不直接拼进 Shot Prompt。"""
    idea = (getattr(project, "idea", None) or "").strip()
    genre = (getattr(project, "genre", None) or "").strip().lower()
    bible: dict[str, str] = {}

    genre_era = {
        "sci_fi": "远未来科幻文明",
        "science_fiction": "远未来科幻文明",
        "cyberpunk": "赛博朋克近未来",
        "fantasy": "奇幻异世界",
        "historical": "历史时期",
        "period": "历史时期",
    }
    if genre in genre_era:
        bible["era"] = genre_era[genre]

    setting = _idea_section(
        idea,
        "核心设定",
        ("故事梗概", "主要角色", "整体视觉", "分镜", "分镜01"),
    )
    visual = _idea_section(
        idea,
        "整体视觉",
        ("分镜", "分镜01", "主要角色", "故事梗概"),
    )
    synopsis = _idea_section(
        idea,
        "故事梗概",
        ("主要角色", "整体视觉", "分镜", "分镜01", "核心设定"),
    )
    if setting:
        bible["worldview"] = _compact_prompt_clause(setting, 240)
    if visual:
        bible["visual_style"] = _compact_prompt_clause(visual, 160)
        # 色彩体系从整体视觉中截取常见色词簇
        color_hits = re.findall(
            r"[冷暖青银灰黑白金铜橙红][^，。；\n]{0,8}(?:色|灰|黑|青)",
            visual,
        )
        if color_hits:
            bible["color_system"] = "、".join(list(dict.fromkeys(color_hits))[:6])

    year_source = " ".join(part for part in (synopsis, idea) if part)
    year_hits = re.findall(
        r"[^。；\n]{0,16}"
        r"(?:推迟了|等待了|过去了|沉默了|拖延了)?"
        r"(?:三百一十二|三百多|三百年|[\d]{2,4}|[一二三四五六七八九十百千零两]{1,10})"
        r"年"
        r"[^。；\n]{0,24}",
        year_source,
    )
    year_hits += re.findall(
        r"[^.\n]{0,24}\b\d{2,4}\s*years?\b[^.\n]{0,24}",
        year_source,
        flags=re.IGNORECASE,
    )
    if year_hits:
        bible["time_span"] = _compact_prompt_clause(year_hits[0], 80)

    style = (getattr(project, "style", None) or "").strip()
    if style:
        bible["project_style"] = style
    return bible


def _storyboard_world_era_prompt(project: Project) -> str:
    """兼容旧调用：Creative Bible 摘要字符串，不再注入生图 Prompt。"""
    bible = _build_creative_bible(project)
    if not bible:
        return ""
    bits = [f"{key}={value}" for key, value in bible.items()]
    return "；".join(bits)


@dataclass(frozen=True)
class AssembledShotPrompt:
    """分层装配后的生图结果。"""

    image_prompt: str
    creative_bible: dict[str, str]
    metadata: dict[str, object]
    debug: PromptDebug


def assemble_shot_prompt(
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
    delivery: str = "DIALOGUE",
    frame: FrameBeat | None = None,
    visibility_by_name: dict[str, str] | None = None,
    absent_character_names: tuple[str, ...] = (),
    visible_props: tuple[str, ...] = (),
    location_version_label: str = "",
    conflicts: list[FrameConflict] | tuple[FrameConflict, ...] = (),
) -> AssembledShotPrompt:
    """按四层装配生图 Prompt：Bible 只进 metadata，画面层只描述摄像机可见内容。"""
    creative_bible = _build_creative_bible(project)
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

    split = split_visual_moments(description)
    frame_brief = frame.visual if frame is not None else compile_static_frame_brief(
        description,
        shot_size=shot_size,
    )
    active_frame = frame
    if active_frame is None:
        active_frame = collapse_to_single_beat(split, shot_size=shot_size)

    if visibility_by_name is not None:
        resolved_visibility = visibility_by_name
    else:
        resolved_visibility = {
            character.name: resolve_visibility(
                character_name=character.name,
                visual_brief=character.visual_brief,
                frame_text=frame_brief,
                delivery=delivery,
                offscreen_names=split.offscreen_names,
                face_hidden_names=split.face_hidden_names,
            )
            for character in characters
        }

    # Voice-over 且画面未点名：不绑定任何角色视觉
    on_camera = [
        character
        for character in characters
        if resolved_visibility.get(character.name) != VISIBILITY_OFF_SCREEN
    ]
    if delivery == "VOICE_OVER" and not any(
        character.name and character.name in frame_brief for character in on_camera
    ):
        on_camera = []

    face_visible = [
        character
        for character in on_camera
        if resolved_visibility.get(character.name) == VISIBILITY_FACE_VISIBLE
    ]
    has_on_camera_dialogue = (
        delivery == "DIALOGUE" and bool(dialogue.strip()) and bool(face_visible)
    )

    removed: list[PromptDebugRemoval] = []
    included: list[str] = []

    if creative_bible:
        removed.append(
            PromptDebugRemoval(
                field="global_creative_bible",
                reason="世界观/时代/风格/色彩仅存 Creative Bible，不进入 image prompt",
                sample="；".join(f"{k}={v}" for k, v in list(creative_bible.items())[:3]),
            )
        )
    for item in split.story_context_removed:
        removed.append(
            PromptDebugRemoval(
                field="story_context",
                reason="剧情背景/时间跨度/法规解释不属于当前静帧可见内容",
                sample=item,
            )
        )
    if dialogue.strip():
        removed.append(
            PromptDebugRemoval(
                field="dialogue",
                reason="对白不进入 image prompt，仅保留可见口型（若有露脸角色）",
                sample=dialogue.strip()[:80],
            )
        )
    if camera_movement:
        removed.append(
            PromptDebugRemoval(
                field="camera_movement",
                reason="运镜过程不进入单帧 image prompt",
                sample=camera_movement,
            )
        )
    audio_cues = tuple(active_frame.audio_cues) if active_frame is not None else ()
    camera_notes = tuple(active_frame.camera_notes) if active_frame is not None else ()
    timeline_notes = tuple(active_frame.timeline_notes) if active_frame is not None else ()
    for cue in audio_cues:
        removed.append(
            PromptDebugRemoval(
                field="audio_cues",
                reason="音频信息进入 metadata，不进入 image prompt",
                sample=cue,
            )
        )
    for note in camera_notes:
        removed.append(
            PromptDebugRemoval(
                field="camera_notes",
                reason="运镜说明进入 metadata，不进入 image prompt",
                sample=note,
            )
        )
    for note in timeline_notes:
        removed.append(
            PromptDebugRemoval(
                field="timeline_notes",
                reason="时间轴说明进入 metadata，不进入 image prompt",
                sample=note,
            )
        )

    absent = absent_character_names or tuple(
        character.name
        for character in characters
        if resolved_visibility.get(character.name) == VISIBILITY_OFF_SCREEN
    )
    if absent:
        removed.append(
            PromptDebugRemoval(
                field="absent_character_constraints",
                reason="不可见角色约束不写入 image prompt，避免诱导生成人脸/表情",
                sample="、".join(absent),
            )
        )
    if delivery == "VOICE_OVER":
        removed.append(
            PromptDebugRemoval(
                field="voice_over_character_visual",
                reason="voice over 角色不传视觉描述与身份模板",
                sample=delivery,
            )
        )
    if not on_camera:
        removed.append(
            PromptDebugRemoval(
                field="portrait_templates",
                reason="visible_characters 为空：禁止输出角色身份、人脸、表情、皮肤、服装要求",
                sample="",
            )
        )

    # --- Layer 2: Location / Asset Context ---
    location_bits = [f"地点 {location}"] if location.strip() else []
    if location_version_label.strip():
        location_bits.append(f"场景版本 {location_version_label.strip()}")
    if time_of_day.strip():
        location_bits.append(f"时段 {time_of_day.strip()}")
    if visible_props:
        location_bits.append(f"可见道具 {'、'.join(visible_props)}")
    location_block = "；".join(location_bits)
    if location_block:
        included.append("location_asset_context")

    # --- Layer 3: Shot Visual Spec ---
    photo_direction = _storyboard_static_frame_direction(
        shot_size=shot_size,
        time_of_day=time_of_day,
        has_on_camera_dialogue=has_on_camera_dialogue,
        has_visible_cast=bool(on_camera),
        is_macro=active_frame.is_macro if active_frame is not None else False,
    )
    dialogue_visual = (
        "人物呈平静说话瞬间的口型与眼神，画面中不出现任何可读文字或字幕。"
        if has_on_camera_dialogue
        else ""
    )
    visual_spec = (
        f"单帧静帧规格：{frame_brief}。"
        f"{shot_size} 景别，{orientation_label} {resolved_ratio}。"
        f"{photo_direction}"
        f"{dialogue_visual}"
    )
    included.extend(
        [
            "shot_visual_spec.frame_brief",
            "shot_visual_spec.shot_size",
            "shot_visual_spec.composition_lighting",
        ]
    )
    if frame_brief:
        included.append("shot_visual_spec.subject_action")

    # --- Layer 4: Character Binding（仅可见角色）---
    identity_block = _storyboard_identity_prompt(
        on_camera,
        visibility_by_name=resolved_visibility,
    )
    if identity_block:
        included.append("character_binding.visible_characters")
    elif not on_camera:
        included.append("character_binding.empty_no_portrait")

    space_line = f"可见主体与空间：{location_block}。" if location_block else ""
    image_prompt = f"{visual_spec}{space_line}{identity_block}"

    metadata: dict[str, object] = {
        "creative_bible": creative_bible,
        "dialogue": dialogue,
        "delivery": delivery,
        "camera_movement": camera_movement,
        "audio_cues": list(audio_cues),
        "camera_notes": list(camera_notes),
        "timeline_notes": list(timeline_notes),
        "absent_character_names": list(absent),
        "story_context_removed": list(split.story_context_removed),
    }
    debug = PromptDebug(
        included=tuple(dict.fromkeys(included)),
        removed=tuple(removed),
        conflicts=tuple(conflicts),
    )
    return AssembledShotPrompt(
        image_prompt=image_prompt,
        creative_bible=creative_bible,
        metadata=metadata,
        debug=debug,
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
    delivery: str = "DIALOGUE",
    frame: FrameBeat | None = None,
    visibility_by_name: dict[str, str] | None = None,
    absent_character_names: tuple[str, ...] = (),
    visible_props: tuple[str, ...] = (),
    location_version_label: str = "",
    conflicts: list[FrameConflict] | tuple[FrameConflict, ...] = (),
) -> str:
    """为低成本分镜生成可执行单帧图像规格（出图前去掉声音/时间轴/运镜/故事上下文）。"""
    return assemble_shot_prompt(
        project,
        description=description,
        dialogue=dialogue,
        location=location,
        time_of_day=time_of_day,
        shot_size=shot_size,
        camera_movement=camera_movement,
        characters=characters,
        aspect_ratio=aspect_ratio,
        delivery=delivery,
        frame=frame,
        visibility_by_name=visibility_by_name,
        absent_character_names=absent_character_names,
        visible_props=visible_props,
        location_version_label=location_version_label,
        conflicts=conflicts,
    ).image_prompt


def _delivery_from_prompt_payload(
    prompt_payload: object,
    *,
    dialogue: str,
) -> str:
    """从 ShotSpec.prompt_json 读取 delivery，缺省时按是否有台词回退。"""
    if isinstance(prompt_payload, dict):
        raw = prompt_payload.get("delivery")
        if raw in {"ACTION", "VOICE_OVER", "DIALOGUE"}:
            return str(raw)
    return "DIALOGUE" if dialogue.strip() else "ACTION"


def _stored_names(prompt_payload: object, key: str) -> tuple[str, ...]:
    if not isinstance(prompt_payload, dict):
        return ()
    values = prompt_payload.get(key)
    if not isinstance(values, list):
        return ()
    return tuple(item for item in values if isinstance(item, str) and item)


def rebuild_shot_frame_prompt(
    session: Session,
    *,
    project: Project,
    shot: Shot,
    spec: ShotSpec,
    prompt_payload: dict[str, object],
    note: str | None = None,
) -> tuple[str, FrameBinding, FrameBeat]:
    """按当前镜头描述重新编译单帧提示词与参考图，沿用最新锁定身份。"""
    delivery = _delivery_from_prompt_payload(prompt_payload, dialogue=shot.dialogue)
    render_mode = prompt_payload.get("render_mode")
    split = split_visual_moments(shot.description)
    beat = collapse_to_single_beat(split, shot_size=shot.shot_size) or FrameBeat(
        visual=shot.description
    )
    if render_mode == RENDER_MODE_BLACK_FRAME:
        beat = FrameBeat(
            visual=beat.visual,
            render_mode=RENDER_MODE_BLACK_FRAME,
            audio_cues=beat.audio_cues,
            camera_notes=beat.camera_notes,
            timeline_notes=beat.timeline_notes,
        )
    characters_by_key = _load_project_characters_by_key(session, project.id)
    stored = _resolve_bound_characters_for_shot(
        session,
        project_id=project.id,
        shot=shot,
        delivery=delivery,
    )
    binding = resolve_frame_bindings(
        session,
        frame=beat,
        delivery=delivery,
        shot_size=shot.shot_size,
        camera_movement=shot.camera_movement,
        frame_named=_characters_from_visual_text(beat.visual, characters_by_key),
        fallback=stored,
        scene_cast=stored,
        offscreen_names=_merge_names(
            _stored_names(prompt_payload, "offscreen_names"), split.offscreen_names
        ),
        face_hidden_names=_merge_names(
            _stored_names(prompt_payload, "face_hidden_names"), split.face_hidden_names
        ),
        location=(
            session.get(LocationVersion, str(spec.location_version_id))
            if spec.location_version_id
            else None
        ),
        props=_specced_prop_versions(session, spec),
    )
    if beat.render_mode == RENDER_MODE_BLACK_FRAME:
        return "", binding, beat
    assembled = assemble_shot_prompt(
        project,
        description=beat.visual,
        dialogue=shot.dialogue,
        location=shot.location,
        time_of_day=shot.time_of_day,
        shot_size=shot.shot_size,
        camera_movement=shot.camera_movement,
        characters=binding.characters,
        aspect_ratio=project.aspect_ratio,
        delivery=delivery,
        frame=beat,
        visibility_by_name=binding.visibility_by_name,
        absent_character_names=binding.absent_names,
        conflicts=binding.conflicts,
    )
    prompt = assembled.image_prompt
    prompt_payload["creative_bible"] = assembled.creative_bible
    prompt_payload["prompt_debug"] = assembled.debug.as_dict()
    for key, value in assembled.metadata.items():
        if key in {
            "audio_cues",
            "camera_notes",
            "timeline_notes",
            "absent_character_names",
            "dialogue",
            "delivery",
            "story_context_removed",
        }:
            prompt_payload[key] = value
    director_intent = prompt_payload.get("director_intent")
    if isinstance(director_intent, dict):
        prompt = f"{prompt}\n{director_intent_prompt_block(director_intent)}"
    if note and note.strip():
        prompt = f"{prompt}\n导演修改意图：{note.strip()}。"
    return prompt, binding, beat


def _specced_prop_versions(session: Session, spec: ShotSpec) -> list[PropVersion]:
    try:
        prop_ids = json.loads(spec.prop_version_ids_json or "[]")
    except json.JSONDecodeError:
        return []
    ordered = [item for item in prop_ids if isinstance(item, str)]
    if not ordered:
        return []
    return list(
        session.scalars(select(PropVersion).where(PropVersion.id.in_(ordered))).all()
    )


def _apply_frame_binding_to_shot(
    shot: Shot,
    *,
    binding: FrameBinding,
    prompt_payload: dict[str, object],
    beat: FrameBeat,
    prompt: str,
) -> None:
    """回写可重新计算的帧级提示数据，不改写 ShotSpec 的连续性锁定快照。"""
    healed_ids = [character.id for character in binding.characters]
    prompt_payload["image_prompt"] = prompt
    prompt_payload["render_mode"] = beat.render_mode
    prompt_payload["character_ids"] = healed_ids
    prompt_payload["character_names"] = [character.name for character in binding.characters]
    prompt_payload["character_visibility"] = binding.visibility_by_name
    prompt_payload["absent_character_names"] = list(binding.absent_names)
    prompt_payload["audio_cues"] = list(beat.audio_cues)
    prompt_payload["camera_notes"] = list(beat.camera_notes)
    prompt_payload["timeline_notes"] = list(beat.timeline_notes)
    prompt_payload["reference_asset_ids"] = binding.reference_asset_ids
    prompt_payload["frame_conflicts"] = [
        {"code": item.code, "severity": item.severity, "message": item.message}
        for item in binding.conflicts
    ]


def resolve_storyboard_take_generation_inputs(
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> tuple[str, list[str], int]:
    """在出图时重建提示词与参考图，确保沿用锁定身份而非过期 JSON 提示。"""
    shot = session.get(Shot, str(payload["shot_id"]))
    spec = session.get(ShotSpec, str(payload["shot_spec_id"]))
    project = session.get(Project, job.project_id)
    if shot is None or spec is None or project is None:
        raise ValueError("分镜任务实体不存在")
    ensure_shot_spec_generation_ready(spec)
    try:
        prompt_payload = json.loads(spec.prompt_json or "{}")
    except json.JSONDecodeError:
        prompt_payload = {}
    if not isinstance(prompt_payload, dict):
        prompt_payload = {}
    note = payload.get("note")
    if isinstance(note, str) and note.strip():
        contract = load_shot_spec_contract(session, spec)
        if note.strip() not in contract.technique.notes:
            contract = contract.model_copy(
                update={
                    "technique": contract.technique.model_copy(
                        update={
                            "notes": (
                                f"{contract.technique.notes}\n导演修改意见：{note.strip()}"
                            ).strip()
                        }
                    )
                }
            )
            write_shot_spec(
                session,
                spec,
                contract,
                actor="system:storyboard-regeneration",
                change_reason="将镜头重生成意见写入结构化导演手法",
                trace_id=job.trace_id,
            )
    prompt, binding, beat = rebuild_shot_frame_prompt(
        session,
        project=project,
        shot=shot,
        spec=spec,
        prompt_payload=prompt_payload,
        note=note if isinstance(note, str) else None,
    )
    prompt_payload["delivery"] = _delivery_from_prompt_payload(
        prompt_payload,
        dialogue=shot.dialogue,
    )
    _apply_frame_binding_to_shot(
        shot,
        binding=binding,
        prompt_payload=prompt_payload,
        beat=beat,
        prompt=prompt,
    )
    _resolved, _report, compiled, _snapshot = compile_shot_spec(
        session,
        spec,
        adapter_name=spec.prompt_adapter or "generic",
        store=True,
    )
    prompt_payload["image_prompt"] = compiled.prompt
    spec.prompt_json = canonical_json(prompt_payload)
    seed = int(payload.get("seed") or 0)
    return compiled.prompt, binding.reference_asset_ids, seed


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


def build_storyboard_agent_drafts(session: Session, job: Job) -> list[StructuredShotSpec]:
    project = project_or_404(session, job.project_id)
    script = session.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == job.project_id, ScriptVersion.status == "APPROVED")
        .order_by(ScriptVersion.version.desc())
    )
    if script is None:
        raise ValueError("已批准剧本不存在")
    characters = list(
        session.scalars(select(Character).where(Character.project_id == project.id)).all()
    )
    characters_by_key = {item.character_key: item for item in characters}
    props = list(
        session.scalars(
            select(PropVersion).where(
                PropVersion.project_id == project.id,
                PropVersion.status == "APPROVED",
            )
        ).all()
    )
    locations = list(
        session.scalars(
            select(LocationVersion).where(
                LocationVersion.project_id == project.id,
                LocationVersion.status == "APPROVED",
            )
        ).all()
    )
    locations_by_name = {item.name: item for item in locations}
    script_scenes = list(
        session.scalars(
            select(ScriptScene)
            .where(ScriptScene.script_version_id == script.id)
            .order_by(ScriptScene.ordinal)
        ).all()
    )
    drafts: list[StructuredShotSpec] = []
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
        last_action_visual = script_scene.purpose.strip()
        location = locations_by_name.get(script_scene.location) or (
            locations[0] if locations else None
        )
        for line, duration_sec in zip(lines, durations, strict=True):
            speaking_character = characters_by_key.get(line.speaker_key)
            speaking_label = speaking_character.name if speaking_character is not None else "旁白"
            delivery = _shot_delivery(line, speaking_character)
            if delivery == "ACTION":
                last_action_visual = _action_visual_text(script_scene.purpose, line)
            description = _visual_description_for_line(
                line,
                delivery=delivery,
                purpose=script_scene.purpose,
                speaking_label=speaking_label,
                action_visual=last_action_visual,
            )
            character_keys = _line_character_keys(
                line,
                characters_by_key=characters_by_key,
                scene_character_keys=scene_character_keys,
                delivery=delivery,
                visual_anchor_text=last_action_visual,
            )
            bound_characters = [characters_by_key[key] for key in character_keys]
            reference_asset_ids: list[str] = []
            for character in bound_characters:
                if not character.locked_candidate_id:
                    continue
                candidate = session.get(CharacterCandidate, character.locked_candidate_id)
                if candidate is not None:
                    reference_asset_ids.append(candidate.asset_id)
            visible_prop_ids = _visible_prop_version_ids(description, props)
            visible_props = [item for item in props if item.id in visible_prop_ids]
            code = f"S{shot_ordinal:02d}"
            shot_size = ("WS", "MS", "MCU", "CU")[(shot_ordinal - 1) % 4]
            camera = ("STATIC", "TRACK", "DOLLY_IN", "PAN")[(shot_ordinal - 1) % 4]
            simple_action = next(
                (
                    item.strip()
                    for item in re.split(r"[，,；;]", description)
                    if item.strip()
                ),
                description,
            )
            draft = build_structured_shot_spec(
                duration_sec=duration_sec,
                narrative_goal=script_scene.purpose,
                description=description,
                action=simple_action,
                environment=script_scene.location,
                location=script_scene.location,
                time_of_day=script_scene.time_of_day,
                shot_size=shot_size,
                camera_movement=camera,
                character_ids=[item.id for item in bound_characters],
                character_names=[item.name for item in bound_characters],
                prop_version_ids=visible_prop_ids,
                prop_names=[item.name for item in visible_props],
                location_version_id=location.id if location is not None else None,
                dialogue=line.text if line.line_type in {"DIALOGUE", "VOICE_OVER"} else "",
                delivery=delivery,
                project_style=project.style,
                aspect_ratio=project.aspect_ratio,
                source_scene_ordinal=script_scene.ordinal,
                source_script_scene_id=script_scene.id,
                source_script_line_ids=[line.id],
                code=code,
                title=script_scene.heading,
                reference_asset_ids=reference_asset_ids,
            )
            drafts.append(draft)
            shot_ordinal += 1
    return drafts


def create_dynamic_storyboard(
    session: Session,
    job: Job,
    *,
    planned_shot_specs: list[StructuredShotSpec] | None = None,
    agent_needs_review: bool = False,
    agent_metadata: dict[str, object] | None = None,
) -> tuple[StoryboardVersion, list[str]]:
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
    characters_by_id = {item.id: item for item in characters}
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
    if planned_shot_specs is None:
        planned_shot_specs = build_storyboard_agent_drafts(session, job)
    planned_by_line_id = {
        item.source.script_line_ids[0]: item
        for item in planned_shot_specs
        if item.source.script_line_ids
    }
    director_intents = confirmed_director_intents_by_scene(session, script=script)
    child_job_ids: list[str] = []
    shot_payloads: list[dict[str, object]] = []
    intent_evidence: dict[str, dict[str, object]] = {}
    intent_consumption: list[dict[str, object]] = []
    shot_ordinal = 1
    for script_scene in script_scenes:
        confirmed_intent = director_intents.get(script_scene.ordinal)
        storyboard_intent = (
            confirmed_intent.snapshot_for("STORYBOARD")
            if confirmed_intent is not None
            else None
        )
        prompt_intent = (
            confirmed_intent.snapshot_for("PROMPT")
            if confirmed_intent is not None
            else None
        )
        if storyboard_intent is not None:
            intent_consumption.append(storyboard_intent)
            intent_evidence[confirmed_intent.change_set_id] = {
                "scene_ordinal": script_scene.ordinal,
                "shot_spec_ids": [],
                "prompt_receipt_hashes": [],
            }
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
        last_action_visual = script_scene.purpose.strip()
        last_action_shot_size: str | None = None
        last_action_camera: str | None = None
        scene_offscreen_names: tuple[str, ...] = ()
        scene_face_hidden_names: tuple[str, ...] = ()
        scene_duration_sec = 0
        for line, duration_sec in zip(lines, durations, strict=True):
            planned_spec = planned_by_line_id.get(line.id)
            speaking_character = characters_by_key.get(line.speaker_key)
            speaking_label = speaking_character.name if speaking_character is not None else "旁白"
            delivery = _shot_delivery(line, speaking_character)
            if delivery == "ACTION":
                last_action_visual = _action_visual_text(script_scene.purpose, line)
            description = _visual_description_for_line(
                line,
                delivery=delivery,
                purpose=script_scene.purpose,
                speaking_label=speaking_label,
                action_visual=last_action_visual,
            )
            line_character_keys = _line_character_keys(
                line,
                characters_by_key=characters_by_key,
                scene_character_keys=scene_character_keys,
                delivery=delivery,
                visual_anchor_text=last_action_visual,
            )
            line_characters = [characters_by_key[key] for key in line_character_keys]
            scene_cast = [characters_by_key[key] for key in scene_character_keys]
            dialogue = line.text if line.line_type in {"DIALOGUE", "VOICE_OVER"} else ""
            if planned_spec is not None:
                description = planned_spec.visual_content.description
                dialogue = planned_spec.audio.dialogue or planned_spec.audio.voice_over
                duration_sec = max(1, round(planned_spec.duration_sec))

            # 「某角色全程不露脸/仅有画外音」在整场生效，先累积再逐帧应用
            split = split_visual_moments(description)
            scene_offscreen_names = _merge_names(
                scene_offscreen_names, split.offscreen_names
            )
            scene_face_hidden_names = _merge_names(
                scene_face_hidden_names, split.face_hidden_names
            )

            cycled_size = (
                planned_spec.camera.shot_size
                if planned_spec is not None
                else ("WS", "MS", "MCU", "CU")[(shot_ordinal - 1) % 4]
            )
            cycled_camera = (
                planned_spec.camera.movement
                if planned_spec is not None
                else ("STATIC", "TRACK", "DOLLY_IN", "PAN")[(shot_ordinal - 1) % 4]
            )
            if delivery == "ACTION":
                planned_size = cycled_size
                planned_camera = cycled_camera
                last_action_shot_size = planned_size
                last_action_camera = planned_camera
            elif last_action_shot_size and last_action_camera:
                # VO / 隐面台词继承同场上一 ACTION 景别运镜，避免冲掉画面语言
                planned_size = last_action_shot_size
                planned_camera = last_action_camera
            else:
                planned_size = cycled_size
                planned_camera = cycled_camera

            # 只有动作行会包含多个视觉时刻；台词/画外音行始终只出一帧
            if planned_spec is not None:
                beats = [
                    FrameBeat(
                        visual=planned_spec.visual_content.description,
                    )
                ]
            elif delivery == "ACTION" and len(split.beats) > 1:
                beats = list(split.beats)
            else:
                collapsed = collapse_to_single_beat(split, shot_size=planned_size)
                beats = [collapsed] if collapsed is not None else [FrameBeat(visual=description)]
            if delivery == "ACTION":
                # 供后续画外音镜头继承的画面状态是最后一帧，而不是整段多时刻文案
                last_action_visual = beats[-1].visual or last_action_visual
            beat_durations = _split_scene_seconds(
                duration_sec,
                [1 if beat.render_mode == RENDER_MODE_BLACK_FRAME else 3 for beat in beats],
            )

            for beat, beat_duration in zip(beats, beat_durations, strict=True):
                code = f"S{shot_ordinal:02d}"
                shot_size = beat.shot_size_hint or planned_size
                camera = planned_camera
                binding = resolve_frame_bindings(
                    session,
                    frame=beat,
                    delivery=delivery,
                    shot_size=shot_size,
                    camera_movement=camera,
                    frame_named=_characters_from_visual_text(beat.visual, characters_by_key),
                    fallback=line_characters,
                    scene_cast=scene_cast,
                    offscreen_names=scene_offscreen_names,
                    face_hidden_names=scene_face_hidden_names,
                    location=location,
                    props=props,
                )
                blocking = blocking_conflicts(binding.conflicts)
                if blocking:
                    raise ValueError(
                        "镜头画面存在必须人工澄清的冲突："
                        + "；".join(item.message for item in blocking)
                    )
                bound_characters = binding.characters
                continuity_characters = (
                    [
                        characters_by_id[character_id]
                        for character_id in planned_spec.continuity.character_ids
                        if character_id in characters_by_id
                    ]
                    if planned_spec is not None
                    else []
                )
                # 旧下游合同要求每个镜头都保存场内角色的锁定版本快照；
                # ShotSpec.continuity 仍只描述本镜真正涉及的角色。
                snapshot_characters = (
                    continuity_characters or bound_characters or scene_cast or characters
                )
                character_ids = [character.id for character in snapshot_characters]
                identity_ids = [
                    character.locked_identity_version_id
                    for character in snapshot_characters
                    if character.locked_identity_version_id
                ]
                look_ids = [
                    character.active_look_version_id
                    for character in snapshot_characters
                    if character.active_look_version_id
                ]
                story_state_ids = [
                    character.active_story_state_version_id
                    for character in snapshot_characters
                    if character.active_story_state_version_id
                ]
                reference_asset_ids = binding.reference_asset_ids
                visible_prop_ids = _visible_prop_version_ids(beat.visual, props)
                shot = Shot(
                    id=str(uuid4()),
                    scene_id=scene.id,
                    code=code,
                    ordinal=shot_ordinal,
                    title=script_scene.heading,
                    description=beat.visual or description,
                    dialogue=dialogue,
                    duration_sec=beat_duration,
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
                scene_duration_sec += beat_duration
                visible_prop_names = [
                    item.name for item in props if item.id in visible_prop_ids and item.name
                ]
                assembled_debug: dict[str, object] = {}
                assembled_bible: dict[str, str] = {}
                if beat.render_mode == RENDER_MODE_BLACK_FRAME:
                    image_prompt = ""
                else:
                    prop_names = tuple(visible_prop_names)
                    assembled = assemble_shot_prompt(
                        project,
                        description=beat.visual,
                        dialogue=dialogue,
                        location=script_scene.location,
                        time_of_day=script_scene.time_of_day,
                        shot_size=shot_size,
                        camera_movement=camera,
                        characters=bound_characters,
                        aspect_ratio=project.aspect_ratio,
                        delivery=delivery,
                        frame=beat,
                        visibility_by_name=binding.visibility_by_name,
                        absent_character_names=binding.absent_names,
                        visible_props=prop_names,
                        location_version_label=(
                            f"v{location.version}" if location is not None else ""
                        ),
                        conflicts=binding.conflicts,
                    )
                    image_prompt = assembled.image_prompt
                    assembled_debug = assembled.debug.as_dict()
                    assembled_bible = assembled.creative_bible
                    if prompt_intent is not None:
                        image_prompt = (
                            f"{image_prompt}\n{director_intent_prompt_block(prompt_intent)}"
                        )
                prompt_payload = _frame_prompt_payload(
                    beat=beat,
                    binding=binding,
                    project=project,
                    description=beat.visual or description,
                    dialogue=dialogue,
                    delivery=delivery,
                    location_name=script_scene.location,
                    time_of_day=script_scene.time_of_day,
                    shot_size=shot_size,
                    camera=camera,
                    location_version_id=location.id if location else None,
                    image_prompt=image_prompt,
                    offscreen_names=scene_offscreen_names,
                    face_hidden_names=scene_face_hidden_names,
                    prompt_intent=prompt_intent,
                    storyboard_intent=storyboard_intent,
                    identity_ids=identity_ids,
                    look_ids=look_ids,
                    story_state_ids=story_state_ids,
                    prop_version_ids=visible_prop_ids,
                )
                prompt_payload["creative_bible"] = assembled_bible
                prompt_payload["prompt_debug"] = assembled_debug
                structured_contract = planned_spec or build_structured_shot_spec(
                    duration_sec=beat_duration,
                    narrative_goal=script_scene.purpose,
                    description=beat.visual or description,
                    action=beat.visual or description,
                    environment=script_scene.location,
                    location=script_scene.location,
                    time_of_day=script_scene.time_of_day,
                    shot_size=shot_size,
                    camera_movement=camera,
                    character_ids=character_ids,
                    character_names=[item.name for item in bound_characters],
                    prop_version_ids=visible_prop_ids,
                    prop_names=visible_prop_names,
                    location_version_id=location.id if location else None,
                    dialogue=dialogue,
                    delivery=delivery,
                    project_style=project.style,
                    aspect_ratio=project.aspect_ratio,
                    source_scene_ordinal=script_scene.ordinal,
                    source_script_scene_id=script_scene.id,
                    source_script_line_ids=[line.id],
                    code=code,
                    title=script_scene.heading,
                    reference_asset_ids=reference_asset_ids,
                )
                if structured_contract.duration_sec != beat_duration:
                    structured_contract = structured_contract.model_copy(
                        update={"duration_sec": float(beat_duration)}
                    )
                if prompt_intent is not None:
                    structured_contract = structured_contract.model_copy(
                        update={
                            "technique": structured_contract.technique.model_copy(
                                update={
                                    "notes": (
                                        f"{structured_contract.technique.notes}\n"
                                        f"{director_intent_prompt_block(prompt_intent)}"
                                    ).strip()
                                }
                            )
                        }
                    )
                spec = ShotSpec(
                    id=str(uuid4()),
                    storyboard_version_id=storyboard.id,
                    shot_id=shot.id,
                    script_scene_id=script_scene.id,
                    script_line_ids_json=canonical_json([line.id]),
                    ordinal=shot_ordinal,
                    description=beat.visual or description,
                    dialogue=dialogue,
                    duration_ms=beat_duration * 1000,
                    shot_size=shot_size,
                    camera_movement=camera,
                    character_look_ids_json=canonical_json(look_ids),
                    location_version_id=location.id if location else None,
                    prop_version_ids_json=canonical_json(visible_prop_ids),
                    prompt_json=canonical_json(prompt_payload),
                    structured_spec_json=canonical_json(
                        structured_contract.model_dump(mode="json")
                    ),
                    prompt_compiled="",
                    prompt_adapter=structured_contract.generation.adapter,
                    compiler_version="prompt-compiler-v1",
                    compiler_input_hash="",
                    prompt_compiled_hash="",
                    validation_report_json="{}",
                    lock_snapshot_json="{}",
                    migration_provenance_json=canonical_json(
                        {
                            "source": "storyboard_agent",
                            "provider": (agent_metadata or {}).get("provider", "deterministic"),
                            "model": (agent_metadata or {}).get(
                                "model", "structured-storyboard-agent-v1"
                            ),
                            "request_id": (agent_metadata or {}).get("request_id"),
                        }
                    ),
                    review_status="VALID",
                    repair_attempts=int((agent_metadata or {}).get("repair_attempts", 0)),
                    content_hash=content_hash(structured_contract.model_dump(mode="json")),
                    status="DRAFT",
                )
                session.add(spec)
                session.flush()
                _resolved, validation_report, compiled, _lock_snapshot = compile_shot_spec(
                    session,
                    spec,
                    adapter_name=structured_contract.generation.adapter,
                    store=True,
                )
                image_prompt = compiled.prompt
                if agent_needs_review or validation_report.needs_review:
                    spec.review_status = "NEEDS_REVIEW"
                    spec.status = "NEEDS_REVIEW"
                    shot.status = "NEEDS_REVIEW"
                else:
                    spec.status = "QUEUED"
                    shot.status = "QUEUED"
                record_shot_spec_revision(
                    session,
                    project_id=project.id,
                    spec=spec,
                    actor="system:storyboard-planner",
                    change_reason="Storyboard Agent 基于严格 ShotSpec 合同生成初始镜头规格",
                    trace_id=job.trace_id,
                )
                if spec.review_status == "NEEDS_REVIEW":
                    session.add(
                        WorkflowNode(
                            id=str(uuid4()),
                            workflow_run_id=workflow.id,
                            node_key=f"storyboard.take.{shot_ordinal}",
                            node_type="REVIEW",
                            entity_type="shot_spec",
                            entity_id=spec.id,
                            job_id=None,
                            status="NEEDS_REVIEW",
                            dependency_keys_json=canonical_json(["storyboard.plan"]),
                            output_json=spec.validation_report_json,
                            degraded=True,
                            error_code="SHOT_SPEC_NEEDS_REVIEW",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    shot_payloads.append(
                        {
                            "shot_spec_id": spec.id,
                            "shot_id": shot.id,
                            "script_line_id": line.id,
                            "duration_ms": spec.duration_ms,
                            "content_hash": spec.content_hash,
                            "render_mode": beat.render_mode,
                            "review_status": spec.review_status,
                        }
                    )
                    shot_ordinal += 1
                    continue
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
                        "render_mode": beat.render_mode,
                        "director_intent": prompt_intent,
                        "seed": int(spec.content_hash[:8], 16),
                    },
                    label=f"{code} · 分镜版本",
                    stage=(
                        "等待合成黑场关键帧"
                        if beat.render_mode == RENDER_MODE_BLACK_FRAME
                        else "等待生成低成本分镜"
                    ),
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
                        "render_mode": beat.render_mode,
                        "director_intent_receipt_hash": (
                            prompt_intent.get("receipt_hash")
                            if prompt_intent is not None
                            else None
                        ),
                    }
                )
                if confirmed_intent is not None:
                    evidence = intent_evidence[confirmed_intent.change_set_id]
                    shot_spec_ids = evidence["shot_spec_ids"]
                    prompt_hashes = evidence["prompt_receipt_hashes"]
                    if isinstance(shot_spec_ids, list):
                        shot_spec_ids.append(spec.id)
                    if isinstance(prompt_hashes, list) and prompt_intent is not None:
                        prompt_hashes.append(prompt_intent["receipt_hash"])
                shot_ordinal += 1
        # 拆帧后镜头总时长可能与初始行时长有偏差，回写场次时长保持一致
        scene.duration_sec = scene_duration_sec or scene.duration_sec
    storyboard.payload_json = canonical_json(
        {
            "schema_version": "storyboard-v2",
            "shot_spec_schema_version": "shot-spec-v1",
            "script_version_id": script.id,
            "visual_bible_version_id": visual_bible.id,
            "shots": shot_payloads,
            "storyboard_agent": agent_metadata or {"provider": "deterministic"},
            "director_intent_consumption": intent_consumption,
        }
    )
    storyboard.content_hash = content_hash(json.loads(storyboard.payload_json))
    needs_review_count = sum(
        1 for item in shot_payloads if item.get("review_status") == "NEEDS_REVIEW"
    )
    if needs_review_count:
        storyboard.status = "NEEDS_REVIEW"
        workflow.status = "WAITING_FOR_REVIEW"
        workflow.current_gate = "G4_STORYBOARD"
    for confirmed_intent in director_intents.values():
        evidence = intent_evidence.get(confirmed_intent.change_set_id)
        if evidence is None or not evidence.get("shot_spec_ids"):
            continue
        update_director_intent_inheritance_receipt(
            session,
            intent=confirmed_intent,
            consumer="STORYBOARD",
            status="INHERITED",
            output_version=storyboard.id,
            evidence={
                **evidence,
                "storyboard_version_id": storyboard.id,
                "planning_mode": "DIRECTIVE_BOUND",
            },
        )
        update_director_intent_inheritance_receipt(
            session,
            intent=confirmed_intent,
            consumer="PROMPT",
            status="INHERITED",
            output_version=storyboard.id,
            evidence={
                **evidence,
                "storyboard_version_id": storyboard.id,
                "prompt_source": "shot_specs.prompt_json.director_intent",
            },
        )
    root_node = session.scalar(
        select(WorkflowNode).where(
            WorkflowNode.workflow_run_id == workflow.id,
            WorkflowNode.node_key == "storyboard.plan",
        )
    )
    if root_node is not None:
        root_node.status = "NEEDS_REVIEW" if needs_review_count else "FAN_OUT_COMPLETE"
        root_node.output_json = canonical_json(
            {
                "child_job_ids": child_job_ids,
                "needs_review_count": needs_review_count,
            }
        )
        root_node.error_code = (
            "SHOT_SPEC_NEEDS_REVIEW" if needs_review_count else None
        )
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
            "needs_review_count": needs_review_count,
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
            director_intent=(
                payload.get("director_intent")
                if isinstance(payload.get("director_intent"), dict)
                else {}
            ),
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
    commit: bool = True,
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
    ensure_shot_spec_generation_ready(spec)
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
    try:
        regen_prompt_payload = json.loads(spec.prompt_json or "{}")
    except json.JSONDecodeError:
        regen_prompt_payload = {}
    if not isinstance(regen_prompt_payload, dict):
        regen_prompt_payload = {}
    cleaned_note = note.strip() if isinstance(note, str) and note.strip() else None
    image_prompt, binding, beat = rebuild_shot_frame_prompt(
        session,
        project=project,
        shot=shot,
        spec=spec,
        prompt_payload=regen_prompt_payload,
        note=cleaned_note,
    )
    blocking = blocking_conflicts(binding.conflicts)
    if blocking:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SHOT_FRAME_CONFLICT",
                "message": "镜头画面存在必须先拆镜或澄清叙事意图的冲突，未提交生图",
                "conflicts": [
                    {"code": item.code, "message": item.message} for item in blocking
                ],
            },
        )
    regen_prompt_payload["delivery"] = _delivery_from_prompt_payload(
        regen_prompt_payload,
        dialogue=shot.dialogue,
    )
    _apply_frame_binding_to_shot(
        shot,
        binding=binding,
        prompt_payload=regen_prompt_payload,
        beat=beat,
        prompt=image_prompt,
    )
    spec.prompt_json = canonical_json(regen_prompt_payload)
    reference_asset_ids = binding.reference_asset_ids
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
            "render_mode": beat.render_mode,
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
    session.flush()
    if commit:
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
        try:
            prompt_payload = json.loads(spec.prompt_json or "{}")
        except json.JSONDecodeError:
            prompt_payload = {}
        structured_contract = load_shot_spec_contract(session, spec)
        validation_report = _json_object(spec.validation_report_json)
        lock_snapshot = _json_object(spec.lock_snapshot_json)
        migration_provenance = _json_object(spec.migration_provenance_json)
        image_prompt = spec.prompt_compiled or ""
        delivery = None
        render_mode = "IMAGE"
        audio_cues: list[str] = []
        camera_notes: list[str] = []
        timeline_notes: list[str] = []
        if isinstance(prompt_payload, dict):
            raw_prompt = prompt_payload.get("image_prompt")
            if not image_prompt and isinstance(raw_prompt, str):
                image_prompt = raw_prompt
            raw_delivery = prompt_payload.get("delivery")
            if raw_delivery in {"ACTION", "VOICE_OVER", "DIALOGUE"}:
                delivery = raw_delivery
            raw_mode = prompt_payload.get("render_mode")
            if isinstance(raw_mode, str) and raw_mode:
                render_mode = raw_mode
            for key, target in (
                ("audio_cues", audio_cues),
                ("camera_notes", camera_notes),
                ("timeline_notes", timeline_notes),
            ):
                values = prompt_payload.get(key)
                if isinstance(values, list):
                    target.extend(item for item in values if isinstance(item, str) and item)
        shots.append(
            {
                "shot_spec_id": spec.id,
                "shot_id": spec.shot_id,
                "scene_id": shot.scene_id if shot else None,
                "shot_lock_version": shot.lock_version if shot else 1,
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
                "shot_spec": structured_contract.model_dump(mode="json"),
                "prompt_compiled": spec.prompt_compiled,
                "prompt_adapter": spec.prompt_adapter,
                "compiler_version": spec.compiler_version,
                "compiler_input_hash": spec.compiler_input_hash,
                "prompt_compiled_hash": spec.prompt_compiled_hash,
                "review_status": spec.review_status,
                "repair_attempts": spec.repair_attempts,
                "validation_report": validation_report,
                "lock_snapshot": lock_snapshot,
                "migration_provenance": migration_provenance,
                "image_prompt": image_prompt,
                "delivery": delivery,
                "render_mode": render_mode,
                "audio_cues": audio_cues,
                "camera_notes": camera_notes,
                "timeline_notes": timeline_notes,
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
    needs_review_ids = list(
        session.scalars(
            select(ShotSpec.id).where(
                ShotSpec.storyboard_version_id == storyboard.id,
                ShotSpec.review_status == "NEEDS_REVIEW",
            )
        ).all()
    )
    if needs_review_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SHOT_SPEC_NEEDS_REVIEW",
                "message": "分镜中仍有未通过导演级校验的镜头，不能批准",
                "details": {"shot_spec_ids": needs_review_ids},
                "user_action": "展开镜头修正结构化字段并重新保存",
            },
        )
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
