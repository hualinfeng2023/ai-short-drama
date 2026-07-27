import base64
import json
import re
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


_AUDIO_NOISE_PATTERNS = (
    r"黑场[，,]?",
    r"三声间隔清晰的有力胎心响起[，,]?",
    r"[^\s，。；]{0,12}(?:胎心|心跳|心音)[^\s，。；]{0,12}(?:响起|搏动|震动|回响)",
    r"(?:画外音|旁白|音效|配乐|音乐|BGM|脚步声|呼吸声|耳语|呢喃|低语|轰鸣|回响)[^。；]*[。；]?",
    r"声音[^。；]{0,24}[。；]?",
    r"人物正在说[：:][^。；]*[。；]?",
    r"正在说[：:][^。；]*[。；]?",
    r"画外音[：:][^。；]*[。；]?",
)

_TIMELINE_NOISE_PATTERNS = (
    r"(?:几秒后|片刻后|随后|然后|接着|此时|此刻|与此同时|渐渐|逐渐|慢慢)[，,]?",
    r"(?:\d+(?:\.\d+)?\s*(?:秒|分钟)后)[，,]?",
    r"淡入[，,]?",
    r"淡出[，,]?",
)

_CAMERA_MOVE_NOISE_PATTERNS = (
    r"(?:跟拍|手持|推镜|拉镜|摇镜|横移|运镜|推进|后拉|甩镜|升降)[^。；]*",
    r"(?:TRACK|DOLLY_IN|DOLLY|PAN|HANDHELD|STATIC)\s*运镜",
    r"背景有轻微运动模糊倾向[^。；]*[。；]?",
    r"像推进中途截取的一帧",
    r"仿佛刚停住的摇镜瞬间",
    r"像纪录片跟拍前的停顿",
)

_FRAME_SPLIT_PATTERNS = (
    r"镜头拉焦(?:露出|到|至)?",
    r"拉焦(?:露出|到|至)",
    r"焦点(?:拉开|推近|落到|移到)",
    r"(?:再|然后|随后)?(?:拉开|推近|推到|拉到|切到|切向|转为|变成|变为|过渡到)",
    r"露出整(?=[间个])",
)


def _strip_patterns(text: str, patterns: tuple[str, ...]) -> str:
    result = text
    for pattern in patterns:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return result


def _normalize_frame_clause(text: str) -> str:
    cleaned = text.strip(" ，,。；;、")
    cleaned = re.sub(r"[，,]{2,}", "，", cleaned)
    cleaned = re.sub(r"[。；]{2,}", "。", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ，,。；;")


def _split_conflicting_frame_beats(text: str) -> tuple[str, str | None]:
    """识别首帧/尾帧冲突，返回 (前段, 后段或 None)。"""
    for pattern in _FRAME_SPLIT_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        before = _normalize_frame_clause(text[: match.start()])
        after = _normalize_frame_clause(text[match.end() :])
        if before and after and before != after:
            return before, after
    return _normalize_frame_clause(text), None


def _prefer_frame_for_shot_size(before: str, after: str | None, shot_size: str) -> str:
    """按景别挑选更匹配的单帧；无冲突则返回 before。"""
    if not after:
        return before
    size = shot_size.upper()
    before_close = any(token in before for token in ("极微距", "微距", "特写", "近景", "表面", "局部"))
    after_wide = any(token in after for token in ("整间", "全景", "广角", "纵深", "两侧", "环境"))
    if size in {"CU", "MCU"}:
        return before if before_close or not after_wide else before
    if size == "WS":
        return after if after_wide or len(after) >= max(8, len(before) // 2) else before
    return after if len(after) >= len(before) else before


def _remove_non_visual_language(text: str) -> str:
    """删除声音、时间轴与运镜过程用语，只留可见描述。"""
    cleaned = _strip_patterns(text, _AUDIO_NOISE_PATTERNS)
    cleaned = _strip_patterns(cleaned, _TIMELINE_NOISE_PATTERNS)
    cleaned = _strip_patterns(cleaned, _CAMERA_MOVE_NOISE_PATTERNS)
    cleaned = re.sub(
        r"(?:正在|继续|开始|结束)?(?:说话|表演|完成台词)[^。；]*[。；]?",
        "",
        cleaned,
    )
    cleaned = re.sub(r"随胎心轻轻震动", "凝结在玻璃上", cleaned)
    return _normalize_frame_clause(cleaned)


def _strip_non_visual_sentences(text: str) -> str:
    """去掉纯叙事/目的句，只保留含可见物象的句子。"""
    visual_cues = (
        "舱", "玻璃", "冰霜", "冷凝", "胚胎", "产房", "婴儿床", "桌", "手", "手套",
        "镜头", "暗部", "房间", "走廊", "窗", "门", "脸", "眼",
        "穿", "站", "坐", "表面", "金属", "识别器", "暖光", "冷光", "阴影",
        "微距", "特写", "全景", "广角", "中景", "近景",
    )
    narrative_cues = (
        "抛出", "交代", "钩子", "冲突", "规则", "完成台词", "保持与锁定",
        "背景设定", "核心钩子", "开场抛出",
    )
    parts = re.split(r"[。；;]", text)
    kept: list[str] = []
    for part in parts:
        clause = part.strip(" ，,")
        if not clause:
            continue
        has_visual = any(token in clause for token in visual_cues)
        has_narrative = any(token in clause for token in narrative_cues)
        if has_narrative and not has_visual:
            continue
        if not has_visual and not has_narrative and len(clause) > 48:
            continue
        kept.append(clause)
    return "。".join(kept)


def compile_single_frame_visual_brief(
    description: str,
    *,
    shot_size: str,
) -> str:
    """把导演阐述压缩为当前这一帧真正可见的画面规格。"""
    text = (description or "").strip()
    if not text:
        return ""
    text = re.sub(r"^画外音覆盖于[：:]", "", text).strip()
    text = re.sub(r"视觉锚点[：:]", "", text).strip()
    # 去掉「角色以…完成台词」表演句，只留后续视觉锚点
    text = re.sub(
        r"^[^。]*以[^。]*状态完成台词[^。]*。?",
        "",
        text,
    ).strip()
    text = _remove_non_visual_language(text)
    text = _strip_non_visual_sentences(text)
    before, after = _split_conflicting_frame_beats(text)
    before = _strip_non_visual_sentences(_remove_non_visual_language(before))
    after_clean = (
        _strip_non_visual_sentences(_remove_non_visual_language(after)) if after else None
    )
    chosen = _prefer_frame_for_shot_size(before, after_clean, shot_size)
    chosen = _strip_non_visual_sentences(_remove_non_visual_language(chosen))
    return chosen or before or (description or "").strip()


def _storyboard_static_frame_direction(
    *,
    shot_size: str,
    time_of_day: str,
    has_on_camera_dialogue: bool,
) -> str:
    """单帧静帧摄影指引：只有景别与光线，不含运镜过程。"""
    shot_language = {
        "WS": "广角全景静帧，环境纵深可见，主体不必居中",
        "MS": "中景静帧，人物与环境信息平衡",
        "MCU": "中近景静帧，上半身与表情主导画面",
        "CU": "近景/微距静帧，焦点在局部材质、眼神或物件表面",
    }.get(shot_size, f"{shot_size} 静帧景别")
    time_lower = time_of_day.lower()
    if any(token in time_lower for token in ("夜", "night", "晚", "凌晨", "午夜")):
        lighting = "冷色实景光与局部暖光并存，主体有明确明暗交界，拒绝平光美颜"
    else:
        lighting = "侧前方自然主光塑造体积，保留真实阴影与材质反光"
    expression = (
        "若人物在画面中：表情克制，口部可呈说话瞬间，禁止摆拍假笑"
        if has_on_camera_dialogue
        else "若人物在画面中：表情克制自然，禁止空眼神与塑料微笑"
    )
    return (
        f"{shot_language}。固定机位单帧，禁止表现推拉摇移过程。"
        f"{expression}。{lighting}。"
        "按电影剧照/实拍静帧理解："
        "非对称构图、空气透视与生活痕迹；皮肤保留毛孔与细微瑕疵，衣料有真实褶皱。"
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
    reference_lines: list[str] = []
    has_visible_face = False
    has_hidden_face = False
    for character in characters:
        brief = character.visual_brief.strip() or "沿用锁定身份五官与发型"
        if _is_face_hidden_brief(character.visual_brief):
            has_hidden_face = True
            reference_lines.append(
                f"- 参考图对应角色：{character.name}（{character.role}）；{brief}。"
                "严格服从上述隐面/局部出镜设定，禁止擅自露脸或另造可识别五官。"
            )
        else:
            has_visible_face = True
            reference_lines.append(
                f"- 参考图对应角色：{character.name}（{character.role}）；{brief}"
            )
    constraints: list[str] = [
        "角色身份锁定（硬约束）：",
        *reference_lines,
    ]
    if has_visible_face:
        constraints.extend(
            (
                "- 输入参考图是每个露脸角色唯一的身份基准。画面中的人物必须与参考图为同一人：",
                "脸型、五官比例、瞳距、鼻梁、唇形、发型核心特征、发色、年龄感与辨识度保持一致。",
            )
        )
    if has_hidden_face:
        constraints.append(
            "- 隐面/局部出镜角色以 visual_brief 为准，不要求五官或唇形可见，禁止用口罩折中出完整人脸。"
        )
    constraints.extend(
        (
            "- 允许改变表情、姿势、景别、光线与背景；禁止换脸、混脸、另造相似替身。",
            "- 当前镜头未绑定的角色不要入镜；不要新增路人抢戏。",
        )
    )
    return "\n".join(constraints)


def _storyboard_photographic_direction(
    *,
    shot_size: str,
    camera_movement: str,
    time_of_day: str,
    has_dialogue: bool,
) -> str:
    """兼容旧调用；单帧出图改走静态指引。"""
    del camera_movement
    return _storyboard_static_frame_direction(
        shot_size=shot_size,
        time_of_day=time_of_day,
        has_on_camera_dialogue=has_dialogue,
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
) -> str:
    """为低成本分镜生成可执行单帧图像规格（出图前去掉声音/时间轴/运镜冲突）。"""
    del camera_movement  # 单帧出图不使用运镜过程信息
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
    frame_brief = compile_single_frame_visual_brief(description, shot_size=shot_size)
    has_on_camera_dialogue = delivery == "DIALOGUE" and bool(dialogue.strip())
    identity_block = _storyboard_identity_prompt(characters)
    cast_names = "、".join(character.name for character in characters) or "无具名角色"
    photo_direction = _storyboard_static_frame_direction(
        shot_size=shot_size,
        time_of_day=time_of_day,
        has_on_camera_dialogue=has_on_camera_dialogue,
    )
    dialogue_visual = (
        "人物呈平静说话瞬间的口型与眼神，画面中不出现任何可读文字或字幕。"
        if has_on_camera_dialogue
        else ""
    )
    return (
        f"单帧静帧规格：{frame_brief}。"
        f"可见主体与空间：出镜角色 {cast_names}；地点 {location}；时段 {time_of_day}。"
        f"{dialogue_visual}"
        f"{shot_size} 景别，{orientation_label} {resolved_ratio}。"
        f"{photo_direction}"
        f"整体风格延续{project.style}，色彩克制、层次丰富。"
        f"{identity_block}"
    )


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
    try:
        prompt_payload = json.loads(spec.prompt_json or "{}")
    except json.JSONDecodeError:
        prompt_payload = {}
    delivery = _delivery_from_prompt_payload(prompt_payload, dialogue=shot.dialogue)
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
        delivery=delivery,
    )
    director_intent = (
        prompt_payload.get("director_intent")
        if isinstance(prompt_payload, dict)
        else None
    )
    if isinstance(director_intent, dict):
        prompt = f"{prompt}\n{director_intent_prompt_block(director_intent)}"
    note = payload.get("note")
    if isinstance(note, str) and note.strip():
        prompt = f"{prompt}\n导演修改意图：{note.strip()}。"
    # 回写实际出图提示词，供分镜详情页核对
    if isinstance(prompt_payload, dict):
        prompt_payload["image_prompt"] = prompt
        prompt_payload["delivery"] = delivery
        if reference_asset_ids:
            prompt_payload["reference_asset_ids"] = reference_asset_ids[:8]
        spec.prompt_json = canonical_json(prompt_payload)
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
        for line, duration_sec in zip(lines, durations, strict=True):
            code = f"S{shot_ordinal:02d}"
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
            cycled_size = ("WS", "MS", "MCU", "CU")[(shot_ordinal - 1) % 4]
            cycled_camera = ("STATIC", "TRACK", "DOLLY_IN", "PAN")[(shot_ordinal - 1) % 4]
            if delivery == "ACTION":
                shot_size = cycled_size
                camera = cycled_camera
                last_action_shot_size = shot_size
                last_action_camera = camera
            elif last_action_shot_size and last_action_camera:
                # VO / 隐面台词继承同场上一 ACTION 景别运镜，避免冲掉画面语言
                shot_size = last_action_shot_size
                camera = last_action_camera
            else:
                shot_size = cycled_size
                camera = cycled_camera
            shot = Shot(
                id=str(uuid4()),
                scene_id=scene.id,
                code=code,
                ordinal=shot_ordinal,
                title=script_scene.heading,
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
                delivery=delivery,
            )
            if prompt_intent is not None:
                image_prompt = f"{image_prompt}\n{director_intent_prompt_block(prompt_intent)}"
            prompt_payload = {
                "description": description,
                "dialogue": dialogue,
                "delivery": delivery,
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
                "director_intent": prompt_intent,
                "storyboard_director_intent": storyboard_intent,
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
            record_shot_spec_revision(
                session,
                project_id=project.id,
                spec=spec,
                actor="system:storyboard-planner",
                change_reason="由已锁定剧本场景与台词生成初始镜头规格",
                trace_id=job.trace_id,
            )
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
                    "director_intent": prompt_intent,
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
    storyboard.payload_json = canonical_json(
        {
            "schema_version": "storyboard-v2",
            "script_version_id": script.id,
            "visual_bible_version_id": visual_bible.id,
            "shots": shot_payloads,
            "director_intent_consumption": intent_consumption,
        }
    )
    storyboard.content_hash = content_hash(json.loads(storyboard.payload_json))
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
    try:
        regen_prompt_payload = json.loads(spec.prompt_json or "{}")
    except json.JSONDecodeError:
        regen_prompt_payload = {}
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
        delivery=_delivery_from_prompt_payload(
            regen_prompt_payload,
            dialogue=shot.dialogue,
        ),
    )
    cleaned_note = note.strip() if isinstance(note, str) and note.strip() else None
    if cleaned_note:
        image_prompt = f"{image_prompt}\n导演修改意图：{cleaned_note}。"
    if isinstance(regen_prompt_payload, dict):
        regen_prompt_payload["image_prompt"] = image_prompt
        if "delivery" not in regen_prompt_payload:
            regen_prompt_payload["delivery"] = _delivery_from_prompt_payload(
                regen_prompt_payload,
                dialogue=shot.dialogue,
            )
        regen_prompt_payload["reference_asset_ids"] = reference_asset_ids
        spec.prompt_json = canonical_json(regen_prompt_payload)
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
        image_prompt = ""
        delivery = None
        if isinstance(prompt_payload, dict):
            raw_prompt = prompt_payload.get("image_prompt")
            if isinstance(raw_prompt, str):
                image_prompt = raw_prompt
            raw_delivery = prompt_payload.get("delivery")
            if raw_delivery in {"ACTION", "VOICE_OVER", "DIALOGUE"}:
                delivery = raw_delivery
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
                "image_prompt": image_prompt,
                "delivery": delivery,
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
