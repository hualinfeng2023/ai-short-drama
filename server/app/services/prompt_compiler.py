import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from app.domain.shot_spec import ShotSpec

COMPILER_VERSION = "prompt-compiler-v3"

_SHOT_SIZE_LABELS = {
    "EWS": "大远景",
    "WS": "全景",
    "MS": "中景",
    "MCU": "中近景",
    "CU": "近景",
    "ECU": "极近景",
    "OTS": "过肩镜头",
    "POV": "主观视角",
}
_MOVEMENT_LABELS = {
    "STATIC": "固定机位",
    "PAN": "横摇轨迹中的关键静帧",
    "TILT": "俯仰摇摄轨迹中的关键静帧",
    "DOLLY_IN": "摄影机推进结束位置的静帧",
    "DOLLY_OUT": "摄影机后移结束位置的静帧",
    "TRACK": "跟拍轨迹中的关键静帧",
    "CRANE": "升降运镜轨迹中的关键静帧",
    "HANDHELD": "克制手持摄影的瞬间",
    "ZOOM": "变焦结束位置的静帧",
}
_STYLE_LABELS = {
    "realistic_cinematic": "写实电影风格",
    "cinematic_realism": "写实电影风格",
}
_GENERIC_PLACEHOLDERS = {
    "沿用项目主色",
    "项目主色",
    "场景强调色",
    "沿用历史场景美术",
    "沿用历史场景氛围",
    "沿用历史色温",
    "沿用历史画面主光方向",
    "沿用角色锁定造型",
    "沿用锁定造型",
    "电影质感",
    "主体清晰",
    "contemporary urban",
    "motivated practical lighting",
    "由旧镜头兼容迁移",
    "入口方向固定",
    "主光方向固定",
    "关键陈设位置固定",
    "中性",
    "正常",
    "无",
    "未指定",
}
_SOURCE_KEY_FRAGMENTS = (
    "_id",
    "_ids",
    "content_hash",
    "reference_asset",
    "lock_version",
    "version",
    "status",
    "negative",
    "forbidden",
    "avoid",
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PromptIR:
    shot_spec: ShotSpec
    project_lock: dict[str, object]
    scene_lock: dict[str, object]
    character_locks: list[dict[str, object]]
    field_locks: list[dict[str, object]]

    def source_manifest(self) -> dict[str, object]:
        return {
            "project_lock": self.project_lock,
            "scene_lock": self.scene_lock,
            "character_locks": self.character_locks,
            "field_locks": self.field_locks,
        }


@dataclass(frozen=True)
class CompiledPrompt:
    prompt: str
    adapter: str
    adapter_version: str
    compiler_version: str
    compiler_input_hash: str
    prompt_hash: str
    provenance: dict[str, object]


class PromptAdapter(Protocol):
    name: str
    version: str

    def compile(self, ir: PromptIR) -> str: ...


def _state_line(label: str, state: object) -> str:
    payload = state.model_dump(mode="json") if hasattr(state, "model_dump") else state
    return f"{label}：{_canonical(payload)}"


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip(" \n\t，,；;。")


def _meaningful(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if text in _GENERIC_PLACEHOLDERS:
        return ""
    for placeholder in sorted(_GENERIC_PLACEHOLDERS, key=len, reverse=True):
        if len(placeholder) >= 4:
            text = text.replace(placeholder, "").strip(" \n\t，,；;。")
    if not text or re.fullmatch(r"(?:EWS|WS|MS|MCU|CU|ECU|OTS|POV)\s*(?:景别|构图)", text):
        return ""
    return text


def _dedupe_text(values: list[str], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    normalized: set[str] = set()
    for value in values:
        text = _meaningful(value)
        key = re.sub(r"[\s，,；;。:：]+", "", text).casefold()
        if not text or key in normalized:
            continue
        normalized.add(key)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def _dedupe_specific(values: list[str], *, limit: int | None = None) -> list[str]:
    """Prefer the more specific clause when one clause wholly contains another."""
    result: list[str] = []
    for value in _dedupe_text(values):
        key = re.sub(r"[\s，,；;。:：]+", "", value).casefold()
        replace_at: int | None = None
        should_skip = False
        for index, existing in enumerate(result):
            existing_key = re.sub(r"[\s，,；;。:：]+", "", existing).casefold()
            if key in existing_key:
                should_skip = True
                break
            if existing_key in key:
                replace_at = index
                break
        if should_skip:
            continue
        if replace_at is not None:
            result[replace_at] = value
        else:
            result.append(value)
        if limit is not None and len(result) >= limit:
            break
    return result


def _style_label(value: object) -> str:
    style = _meaningful(value)
    normalized = style.casefold().replace("-", "_").replace(" ", "_")
    return _STYLE_LABELS.get(normalized, style)


def _shot_text_blob(shot: ShotSpec) -> str:
    return " ".join(
        _clean_text(value)
        for value in (
            shot.narrative_goal,
            shot.visual_content.description,
            shot.visual_content.action,
            shot.visual_content.environment,
            shot.start_state.action_state,
            shot.start_state.environment_state,
            shot.end_state.action_state,
            shot.end_state.environment_state,
        )
        if _clean_text(value)
    )


def _scoped_visual_direction(values: list[str], shot: ShotSpec) -> list[str]:
    """Remove project-wide future/phase clauses that do not describe this frame."""
    shot_text = _shot_text_blob(shot)
    scoped: list[str] = []
    for value in values:
        clauses = [
            item.strip()
            for item in re.split(r"(?<=[。！？!?])|[；;]\s*", value)
            if item.strip()
        ]
        for clause in clauses:
            conditional = re.search(r"([^，。；]{2,12})后[，,]", clause)
            if conditional:
                anchor = conditional.group(1).strip()
                anchor_tail = anchor[-4:]
                if anchor not in shot_text and anchor_tail not in shot_text:
                    continue
            if any(marker in clause for marker in ("后半段", "结尾阶段", "最终镜头")) and not any(
                marker in shot_text for marker in ("后半段", "结尾", "最终")
            ):
                continue
            scoped.append(clause)
    return _dedupe_specific(scoped, limit=6)


def _visual_text_values(
    value: object,
    *,
    limit: int = 8,
    _key: str = "",
) -> list[str]:
    """Extract human-readable visual facts while keeping IDs/hashes in provenance only."""
    if any(fragment in _key.casefold() for fragment in _SOURCE_KEY_FRAGMENTS):
        return []
    if isinstance(value, str):
        text = _meaningful(value)
        return [text] if text else []
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_visual_text_values(item, limit=limit - len(values)))
            if len(values) >= limit:
                break
        return _dedupe_text(values, limit=limit)
    if isinstance(value, dict):
        values = []
        for key, item in value.items():
            values.extend(
                _visual_text_values(
                    item,
                    limit=limit - len(values),
                    _key=str(key),
                )
            )
            if len(values) >= limit:
                break
        return _dedupe_text(values, limit=limit)
    return []


def _paragraph(label: str, values: list[str]) -> str:
    clauses = _dedupe_text(values)
    return f"{label}：" + "；".join(clauses) + "。" if clauses else ""


def _negative_terms(negative_prompt: str) -> list[str]:
    return [
        item
        for item in re.split(r"[\s，,；;、/]+", negative_prompt)
        if len(item) >= 2
    ]


def _without_negative_conflicts(text: str, negative_terms: list[str]) -> str:
    if not text or not negative_terms:
        return text
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?])", text)
        if item.strip()
    ]
    kept = [
        sentence
        for sentence in sentences
        if not any(term in sentence for term in negative_terms)
    ]
    return "".join(kept)


def _visual_character_brief(value: object) -> str:
    text = _meaningful(value)
    clauses = [
        item.strip()
        for item in re.split(r"[，,；;。]", text)
        if item.strip()
        and not any(marker in item for marker in ("声音", "语气", "声线", "口音"))
    ]
    return "，".join(clauses)


def _representative_image_values(shot: ShotSpec) -> list[str]:
    description = shot.visual_content.description
    has_timeline = any(
        marker in description
        for marker in ("黑场", "镜头拉", "转场", "随后", "然后", "切到", "切回")
    )
    if has_timeline:
        return _dedupe_text(
            [
                shot.end_state.action_state,
                shot.end_state.environment_state,
                shot.visual_content.environment,
                *shot.visual_content.visible_props,
            ]
        )
    return _dedupe_text(
        [
            description,
            shot.visual_content.action,
            shot.end_state.action_state,
            shot.end_state.environment_state,
            *shot.visual_content.visible_props,
        ]
    )


def _lens_execution(lens_mm: int) -> list[str]:
    if lens_mm <= 28:
        return [
            f"{lens_mm}mm广角焦段",
            "保留明确的近大远小关系与纵深感，边缘畸变自然且克制",
        ]
    if lens_mm <= 40:
        return [
            f"{lens_mm}mm中广角焦段",
            "空间纵深清楚，近景透视存在但不夸张",
        ]
    if lens_mm <= 60:
        return [
            f"{lens_mm}mm标准焦段",
            "自然透视，主体与环境比例不过度夸张",
        ]
    if lens_mm <= 100:
        return [
            f"{lens_mm}mm中长焦焦段",
            "透视适度压缩，主体与背景层次分离",
        ]
    return [
        f"{lens_mm}mm长焦焦段",
        "明显压缩空间关系，焦点层级必须清晰",
    ]


def _shot_scale_execution(shot_size: str) -> str:
    if shot_size in {"EWS", "WS"}:
        return "环境承担主要叙事信息，主体保持清晰视觉锚点，前景、中景、远景层次可辨"
    if shot_size in {"MS", "MCU", "OTS"}:
        return "主体动作与环境关系同时可读，背景细节服务叙事但不争夺焦点"
    if shot_size in {"CU", "ECU"}:
        return "焦点锁定关键表情或物理细节，背景退居次要层次且不丢失空间归属"
    if shot_size == "POV":
        return "视点严格服从角色观察位置，前景遮挡与空间尺度保持真实"
    return ""


def _camera_prompt_values(shot: ShotSpec) -> list[str]:
    lens_values = _lens_execution(shot.camera.lens_mm)
    focus = _meaningful(shot.camera.focus)
    return _dedupe_specific(
        [
            (
                f"{shot.camera.angle}机位，"
                f"{_SHOT_SIZE_LABELS.get(shot.camera.shot_size, shot.camera.shot_size)}"
            ),
            *lens_values,
            _MOVEMENT_LABELS.get(shot.camera.movement, shot.camera.movement),
            shot.visual_content.composition,
            shot.camera.framing,
            focus,
            _shot_scale_execution(shot.camera.shot_size),
            "透视汇聚、人物比例、遮挡边界与摄影机高度保持物理一致",
        ]
    )


def _director_constraints(notes: object) -> tuple[list[str], list[str]]:
    text = _clean_text(notes).replace("\\n", "\n")
    if not text:
        return [], []
    text = re.sub(r"(?:导演修改意见|导演意见|修改意见)\s*[：:]\s*", "", text)
    positive: list[str] = []
    negative: list[str] = []
    for sentence in re.split(r"[\n]+|(?<=[。！？!?])\s*", text):
        for clause in re.split(r"[；;]\s*", sentence):
            item = _meaningful(clause)
            if not item:
                continue
            if any(marker in item for marker in ("禁止", "不得", "避免", "不要")):
                negative.append(item)
            else:
                positive.append(item)
    deduped_positive: list[str] = []
    for item in _dedupe_specific(positive):
        body = re.sub(r"^(?:严格表现|保留|确保|呈现)", "", item).lstrip("：:")
        if body and any(body in existing for existing in deduped_positive):
            continue
        deduped_positive.append(item)
    return deduped_positive[:6], _dedupe_specific(negative, limit=8)


def _expanded_negative_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _meaningful(value)
        if not text:
            continue
        stripped = re.sub(
            r"^(?:禁止|不得|避免|不要)(?:出现|使用|添加|改写)?",
            "",
            text,
        ).strip(" ：:")
        if stripped != text and re.search(r"[、，,]|(?:以及|及|和)", stripped):
            result.extend(
                item.strip(" ：:")
                for item in re.split(r"[、，,]|(?:以及|及|和)", stripped)
                if len(item.strip(" ：:")) >= 2
            )
        else:
            result.append(stripped or text)
    return _dedupe_text(result)


def _visible_physical_responses(ir: PromptIR) -> list[str]:
    corpus = "；".join(
        [
            *_representative_image_values(ir.shot_spec),
            *_scene_asset_values(ir),
            *_character_prompt_lines(ir),
        ]
    )
    responses: list[str] = []
    rules = (
        (
            ("冷凝水",),
            "冷凝水沿重力方向附着，液滴边缘形成克制高光，并折射附近已声明光源",
        ),
        (
            ("冰霜", "覆霜"),
            "霜层形成厚薄不均的半透明散射，边缘状态与低温表面一致",
        ),
        (
            ("玻璃",),
            "玻璃同时保留受控反射与透射，厚度、表面痕迹和背后主体关系清楚",
        ),
        (
            ("金属",),
            "金属高光随表面粗糙度变化，不出现塑料般均匀反光",
        ),
        (
            ("雨水", "暴雨", "湿润", "打湿"),
            "湿润表面产生方向一致的低亮度反射，水痕服从重力与接触面",
        ),
        (
            ("积灰", "磨损", "使用痕迹"),
            "积灰、磨损与触碰痕迹只出现在符合使用逻辑的边缘、接缝和接触面",
        ),
        (
            ("织物", "风衣", "制服", "衣服"),
            "织物褶皱、张力与磨损服从人物姿态和面料重量",
        ),
    )
    for markers, response in rules:
        if any(marker in corpus for marker in markers):
            responses.append(response)
    skin_is_visible = any(marker in corpus for marker in ("皮肤", "面部")) or (
        "双手" in corpus and "手套" not in corpus
    )
    if skin_is_visible:
        responses.append("可见皮肤保留毛孔、细纹和自然色差，不过度磨皮")
    return _dedupe_specific(responses, limit=6)


def _realistic_cinematic_direction(style: str) -> tuple[list[str], list[str]]:
    normalized = style.casefold().replace("-", "_").replace(" ", "_")
    if normalized not in {
        "realistic_cinematic",
        "cinematic_realism",
        "写实电影",
        "写实电影风格",
    }:
        return [], []
    return (
        [
            "真实电影摄影",
            "实拍置景与物理灯光",
            "所有亮部都能回溯到画内或场外的明确光源，方向、遮挡、反射与距离衰减一致",
            "已声明材质按照真实粗糙度、反射、透射或次表面反应呈现",
            "表面痕迹只反映已声明的年代、用途、湿度与使用状态",
            "高动态范围数字电影摄影，亮部柔和滚降，暗部保留层次而不过度提亮",
            "真实镜头畸变与自然景深，焦平面和虚化过渡符合当前焦段",
            "轻微高光溢出，不过度锐化",
            "细腻电影颗粒、自然色彩分离与克制调色",
        ],
        [
            "概念原画",
            "插画",
            "游戏 CG",
            "3D 渲染",
            "塑料质感",
            "无设定依据的霓虹灯",
            "悬浮 UI",
            "可读文字",
            "像素化",
        ],
    )


def _character_prompt_lines(ir: PromptIR) -> list[str]:
    shot = ir.shot_spec
    active_ids = set(shot.continuity.character_ids)
    lines: list[str] = []
    for item in ir.character_locks:
        character_id = str(item.get("character_id") or "")
        if active_ids and character_id not in active_ids:
            continue
        name = _meaningful(item.get("name")) or "锁定角色"
        visual_brief = _visual_character_brief(item.get("visual_brief"))
        is_partial_or_hidden = any(
            marker in visual_brief
            for marker in ("不露脸", "不露出面部", "仅出现", "局部出镜", "背对镜头")
        )
        facts = _dedupe_text(
            [
                visual_brief,
                *(
                    []
                    if is_partial_or_hidden
                    else [
                        *_visual_text_values(item.get("identity"), limit=4),
                        *_visual_text_values(item.get("look"), limit=4),
                        *_visual_text_values(item.get("story_state"), limit=3),
                    ]
                ),
            ],
            limit=8,
        )
        if facts:
            lines.append(f"{name}：{'，'.join(facts)}")
        else:
            lines.append(f"{name}严格沿用已锁定身份、造型和当前故事状态")
    return lines


def _scene_asset_values(ir: PromptIR) -> list[str]:
    scene_lock = ir.scene_lock
    values: list[str] = []
    location = scene_lock.get("location")
    if isinstance(location, dict):
        values.extend(
            [
                _meaningful(location.get("name")),
                *_visual_text_values(location.get("visual_facts"), limit=8),
            ]
        )
    props = scene_lock.get("props")
    if isinstance(props, list):
        for prop in props:
            if not isinstance(prop, dict):
                continue
            prop_name = _meaningful(prop.get("name"))
            prop_facts = _visual_text_values(prop.get("visual_facts"), limit=4)
            if prop_name or prop_facts:
                values.append(
                    "，".join([item for item in [prop_name, *prop_facts] if item])
                )
    return _dedupe_text(values, limit=12)


def _base_sections(ir: PromptIR) -> list[str]:
    shot = ir.shot_spec
    character_lock_lines = [
        (
            f"- {item.get('name') or item.get('character_id')}："
            f"identity={item.get('identity_version_id') or '未锁定'}，"
            f"look={item.get('look_version_id') or '未锁定'}，"
            f"story_state={item.get('story_state_version_id') or '未锁定'}"
        )
        for item in ir.character_locks
    ]
    sections = [
        f"镜头时长：{shot.duration_sec:g} 秒",
        f"叙事目标：{shot.narrative_goal}",
        f"画面内容：{shot.visual_content.description}",
        f"主体与动作：{shot.visual_content.action}",
        f"环境与构图：{shot.visual_content.environment}；{shot.visual_content.composition}",
        (
            "摄影机："
            f"{shot.camera.shot_size}，{shot.camera.movement}，{shot.camera.angle}，"
            f"{shot.camera.lens_mm}mm，{shot.camera.framing}，焦点 {shot.camera.focus}"
        ),
        (
            "灯光："
            f"{shot.lighting.style}；主光 {shot.lighting.key_light}；"
            f"色温 {shot.lighting.color_temperature}；对比 {shot.lighting.contrast}；"
            f"氛围 {shot.lighting.atmosphere}"
        ),
        (
            "美术："
            f"{shot.art_direction.visual_style}；色板 {'、'.join(shot.art_direction.palette)}；"
            f"材质 {shot.art_direction.texture}；"
            f"场景设计 {shot.art_direction.production_design}；"
            f"服装 {shot.art_direction.wardrobe or '沿用角色锁定造型'}"
        ),
        (
            "导演手法："
            f"节奏 {shot.technique.pacing}；入镜 {shot.technique.transition_in}；"
            f"出镜 {shot.technique.transition_out}；{shot.technique.notes}"
        ),
        f"表演：{shot.performance.ensemble_blocking}；{shot.performance.emotion_arc}",
        (
            "声音："
            f"对白 {shot.audio.dialogue or '无'}；画外音 {shot.audio.voice_over or '无'}；"
            f"环境声 {'、'.join(shot.audio.ambience) or '无'}；"
            f"音效 {'、'.join(shot.audio.sfx) or '无'}；音乐 {shot.audio.music or '无'}"
        ),
        _state_line("镜头起始状态", shot.start_state),
        _state_line("镜头结束状态", shot.end_state),
        (
            "连续性硬约束："
            f"人物 {','.join(shot.continuity.character_ids) or '无'}；"
            f"道具 {','.join(shot.continuity.prop_version_ids) or '无'}；"
            f"轴线 {shot.camera.axis_id or '未指定'}/{shot.camera.axis_side}；"
            f"{'；'.join(shot.continuity.must_match)}"
        ),
        (
            "生成约束："
            f"画幅 {shot.generation.aspect_ratio}；分辨率 {shot.generation.resolution}；"
            f"{shot.generation.fps} fps；负面约束 {shot.generation.negative_prompt or '无'}"
        ),
    ]
    if character_lock_lines:
        sections.append("角色锁（不得改写身份与造型）：\n" + "\n".join(character_lock_lines))
    if ir.project_lock:
        sections.append("Project Global Lock：" + _canonical(ir.project_lock))
    if ir.scene_lock:
        sections.append("Scene Lock：" + _canonical(ir.scene_lock))
    return sections


class GenericPromptAdapter:
    name = "generic"
    version = "generic-image-v3"

    def compile(self, ir: PromptIR) -> str:
        shot = ir.shot_spec
        negative_terms = _negative_terms(shot.generation.negative_prompt)
        project_world = [
            _without_negative_conflicts(value, negative_terms)
            for value in _visual_text_values(ir.project_lock.get("world"), limit=5)
        ]
        project_visual = _scoped_visual_direction(
            [
                _without_negative_conflicts(value, negative_terms)
                for value in _visual_text_values(
                    ir.project_lock.get("visual_direction"),
                    limit=6,
                )
            ],
            shot,
        )
        project_style = _style_label(ir.project_lock.get("style"))
        shot_style = _style_label(shot.art_direction.visual_style)
        realistic_style, realistic_negatives = _realistic_cinematic_direction(
            project_style or shot_style
        )
        scene_assets = _scene_asset_values(ir)
        character_lines = _character_prompt_lines(ir)
        director_positive, director_negative = _director_constraints(
            shot.technique.notes
        )

        world_and_scene = _paragraph(
            "世界与场景",
            [
                *project_world,
                shot.start_state.location,
                shot.start_state.time_of_day,
                shot.visual_content.environment,
                *scene_assets,
            ],
        )
        visible_content = _paragraph(
            "画面主体",
            _representative_image_values(shot),
        )
        character_continuity = _paragraph("角色连续性", character_lines)
        camera = _paragraph(
            "摄影机与构图",
            _camera_prompt_values(shot),
        )
        physical_image = _paragraph(
            "光线、材质与美术",
            [
                shot.lighting.style,
                shot.lighting.key_light,
                shot.lighting.fill_light,
                shot.lighting.color_temperature,
                (
                    "主次亮度层级清楚，亮部不过曝，暗部保留可读细节"
                    if _meaningful(shot.lighting.contrast) == "电影感对比"
                    else shot.lighting.contrast
                ),
                shot.lighting.atmosphere,
                shot.art_direction.production_design,
                shot.art_direction.texture,
                *shot.art_direction.palette,
                *shot.technique.practical_effects,
                *shot.technique.vfx,
                *_visible_physical_responses(ir),
                "光源位置、照射方向、遮挡关系、反射路径与亮度衰减必须彼此一致",
                "已声明材质必须呈现与其粗糙度、透射性、湿度和使用状态一致的高光、阴影与表面痕迹",
            ],
        )
        photographic_finish = _paragraph(
            "摄影质感",
            [
                project_style,
                shot_style,
                *project_visual,
                *realistic_style,
                f"{shot.generation.resolution}，{shot.generation.aspect_ratio}",
            ],
        )
        director_notes = _paragraph(
            "导演补充",
            [shot.narrative_goal, *director_positive],
        )
        negative = _paragraph(
            "明确禁止",
            _expanded_negative_terms(
                [
                    *_negative_terms(shot.generation.negative_prompt),
                    *director_negative,
                    *realistic_negatives,
                    "新增未声明人物、道具或场景",
                    "改写锁定角色身份、服装或故事状态",
                ]
            ),
        )
        return "\n".join(
            paragraph
            for paragraph in (
                "生成一张且仅一张可直接指导概念生图的电影静帧。"
                "所有世界、角色和场景事实均服从已批准或已锁定版本，不得自行补写关键设定。",
                world_and_scene,
                visible_content,
                character_continuity,
                camera,
                physical_image,
                photographic_finish,
                director_notes,
                negative,
            )
            if paragraph
        )


class VeoPromptAdapter:
    name = "veo"
    version = "veo-v1"

    def compile(self, ir: PromptIR) -> str:
        shot = ir.shot_spec
        return "\n".join(
            [
                "Veo 视频生成任务。用连续时间描述单个完整镜头；优先保持时序、物理运动、"
                "角色身份和同步声音，不要切换成多镜头蒙太奇。",
                *_base_sections(ir),
                (
                    "Veo 时序指令："
                    f"0 秒严格对应 start_state；在 {shot.duration_sec:g} 秒内仅完成已声明动作；"
                    f"最后一帧严格落在 end_state。声音与可见动作按 sync_notes 同步。"
                ),
            ]
        )


class KlingPromptAdapter:
    name = "kling"
    version = "kling-v1"

    def compile(self, ir: PromptIR) -> str:
        shot = ir.shot_spec
        return "\n".join(
            [
                "Kling 视频生成任务。把运动幅度、主体轨迹、摄影机轨迹和首尾帧稳定性写成"
                "可执行约束；避免动作叠加、身份漂移与末帧跳变。",
                *_base_sections(ir),
                (
                    "Kling 运动指令："
                    f"主体动作只执行“{shot.visual_content.action}”；"
                    f"摄影机只执行 {shot.camera.movement}；"
                    "首帧、末帧分别锁定结构化 start_state 与 end_state。"
                ),
            ]
        )


class SeedancePromptAdapter:
    name = "seedance"
    version = "seedance-v1"

    def compile(self, ir: PromptIR) -> str:
        shot = ir.shot_spec
        return "\n".join(
            [
                "Seedance 图生视频任务。参考图只确定首帧主体身份、服装、场景与构图；"
                "视频阶段只执行 ShotSpec 声明的动作和运镜，禁止新增主体、文字、字幕和转场拼贴。",
                *_base_sections(ir),
                (
                    "Seedance 稳定性指令："
                    f"总时长 {shot.duration_sec:g} 秒，{shot.camera.movement} 运镜，"
                    "人物脸型、五官、发型、服装，道具外观及背景布局全程稳定；"
                    "末帧必须符合 end_state，避免闪烁、融脸、肢体断裂和物体凭空出现。"
                ),
            ]
        )


ADAPTERS: dict[str, PromptAdapter] = {
    adapter.name: adapter
    for adapter in (
        GenericPromptAdapter(),
        VeoPromptAdapter(),
        KlingPromptAdapter(),
        SeedancePromptAdapter(),
    )
}


class PromptCompiler:
    def compile(
        self,
        shot_spec: ShotSpec,
        *,
        adapter_name: str | None = None,
        project_lock: dict[str, object] | None = None,
        scene_lock: dict[str, object] | None = None,
        character_locks: list[dict[str, object]] | None = None,
        field_locks: list[dict[str, object]] | None = None,
    ) -> CompiledPrompt:
        resolved_adapter = adapter_name or shot_spec.generation.adapter
        adapter = ADAPTERS.get(resolved_adapter)
        if adapter is None:
            raise ValueError(f"不支持的 Prompt Adapter：{resolved_adapter}")
        ir = PromptIR(
            shot_spec=shot_spec,
            project_lock=project_lock or {},
            scene_lock=scene_lock or {},
            character_locks=character_locks or [],
            field_locks=field_locks or [],
        )
        compiler_input = {
            "shot_spec": shot_spec.model_dump(mode="json"),
            "locks": ir.source_manifest(),
            "compiler_version": COMPILER_VERSION,
            "adapter": adapter.name,
            "adapter_version": adapter.version,
        }
        compiler_input_hash = _hash(compiler_input)
        prompt = adapter.compile(ir).strip()
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return CompiledPrompt(
            prompt=prompt,
            adapter=adapter.name,
            adapter_version=adapter.version,
            compiler_version=COMPILER_VERSION,
            compiler_input_hash=compiler_input_hash,
            prompt_hash=prompt_hash,
            provenance={
                "compiler_input_hash": compiler_input_hash,
                "compiler_version": COMPILER_VERSION,
                "adapter": adapter.name,
                "adapter_version": adapter.version,
                "sources": ir.source_manifest(),
            },
        )
