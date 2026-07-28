"""把导演阐述拆成互不冲突的单帧画面规格。

一个生图任务只能描述一个静态时刻，因此本模块负责：
1. 把含多个视觉时刻的文案拆成独立 FrameBeat；
2. 从每个 FrameBeat 里剥离声音、时间轴、运镜过程与叙事目的，只留当前可见信息；
3. 判定角色在当前画面里的可见范围，供参考图选择使用；
4. 在提交生图前检查明显冲突。

本模块不访问数据库，全部为纯函数，便于单独测试。
"""

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 词表：全部按语言现象归类，不针对任何具体剧本
# ---------------------------------------------------------------------------

# 天然只存在于听觉通道的词，出现即整句剔除
_INHERENT_AUDIO_TOKENS = (
    "画外音",
    "旁白",
    "音效",
    "配乐",
    "音乐",
    "BGM",
    "对白",
    "台词",
    "声音语气",
    "语气",
    "口白",
)

# 可以是画面主体也可以是声源的词，需结合谓语判断
_AUDIO_NOUNS = (
    "胎心",
    "心跳",
    "心音",
    "脚步声",
    "呼吸声",
    "呼吸",
    "耳语",
    "呢喃",
    "低语",
    "轰鸣",
    "回响",
    "警报",
    "铃声",
    "雷声",
    "雨声",
    "风声",
    "枪声",
    "爆炸声",
    "哭声",
    "笑声",
    "尖叫",
    "嗡鸣",
    "嘶鸣",
    "声响",
    "声音",
    "人声",
    "节拍",
    "鼓点",
)

# 听觉谓语：出现在句尾说明该句在描述声音事件
_SOUND_VERBS = (
    "响起",
    "作响",
    "回响",
    "传来",
    "奏起",
    "播放",
    "盖过",
    "渐入",
    "渐出",
    "鸣响",
    "炸响",
    "响",
)

# 时间轴与时序推进用语
_TIMELINE_TOKENS = (
    "随后",
    "然后",
    "接着",
    "此时",
    "此刻",
    "与此同时",
    "同时",
    "渐渐",
    "逐渐",
    "慢慢",
    "片刻后",
    "几秒后",
    "最终",
    "终于",
    "全程",
    "淡入",
    "淡出",
)
_TIMELINE_PATTERNS = (
    r"\d+(?:\.\d+)?\s*(?:秒|分钟|分|小时)(?:后|内|之后)",
    r"第\s*\d+\s*(?:秒|帧|分钟)",
)

# 运镜过程用语：单帧不能表现运动过程
_CAMERA_MOVE_TOKENS = (
    "拉焦",
    "推镜",
    "拉镜",
    "摇镜",
    "甩镜",
    "横移",
    "运镜",
    "推进",
    "推近",
    "后拉",
    "拉远",
    "拉开",
    "升降",
    "跟拍",
    "手持",
    "变焦",
    "移焦",
    "环绕",
    "俯冲",
)

# 叙事目的用语：说明这句在讲剧情作用，不是画面
_NARRATIVE_VERBS = ("抛出", "交代", "交待", "铺垫", "点出", "引出", "埋下", "建立", "收束")
_NARRATIVE_OBJECTS = (
    "钩子",
    "悬念",
    "背景",
    "设定",
    "冲突",
    "信息",
    "主题",
    "动机",
    "反转",
    "情绪",
)

# 剧情/世界观上下文：只进 metadata，不进静帧生图
_STORY_CONTEXT_TOKENS = (
    "人口守恒法",
    "人口守恒",
    "守恒法",
    "核心设定",
    "世界观",
    "故事梗概",
    "剧情背景",
    "背景设定",
    "设定解释",
)
_STORY_YEAR_PATTERNS = (
    r"永生\s*\d+\s*年",
    r"永生\s*[一二三四五六七八九十百千零两\d]+\s*年",
    r"(?:推迟了|等待了|过去了|沉默了|拖延了)[^，。；]{0,12}年",
    r"三百[一二三四五六七八九十零两白千]*年",
    r"\b\d{2,4}\s*years?\b",
)
_PSYCHOLOGY_TOKENS = (
    "内心",
    "心理",
    "犹豫",
    "纠结",
    "决心",
    "情感波动",
    "情绪波动",
    "心里想",
    "暗自",
    "思索",
)

# 拉开/拉远类衔接：前一拍若混有微距与空间，空间归入下一拍
_PULL_BACK_TOKENS = ("拉远", "后拉", "拉开", "拉焦", "露出")

# 表演模板用语：与当前画面是否有人可见无关的通用人像要求
_PERFORMANCE_PATTERNS = (
    r"[^，。；]*以[^，。；]*状态完成台词[^，。；]*",
    r"(?:正在|继续|开始|结束)?(?:说话|表演|完成台词)[^，。；]*",
    r"保持与锁定身份参考图为同一人",
    r"视觉锚点",
)

# 触发拆帧的衔接词：出现即说明画面已经切换到下一个时刻
# 时序词必须先于「后拉/拉远」，避免「随后拉远」被切成「后拉」+「随远」
_TRANSITION_PATTERNS = (
    r"(?:随后|然后|接着|片刻后|几秒后|最终|终于)(?:镜头)?(?:拉焦|推近|后拉|拉远|拉开|推进)?(?:露出|到|至|后)?",
    r"镜头(?:拉焦|推近|后拉|拉远|拉开)(?:露出|到|至|后)?",
    r"(?:拉焦|拉远|后拉|推近|推进)(?:露出|到|至)?",
    r"焦点(?:拉开|推近|落到|移到|转到)",
    r"(?:切到|切向|切至|跳切到|转为|转到|变成|变为|过渡到|叠化到)",
    r"露出整(?=[间个座片])",
    r"\d+(?:\.\d+)?\s*(?:秒|分钟)后",
)

# 景别尺度：极微距与全景不能共存于一帧
_MACRO_TOKENS = ("极微距", "微距", "特写", "表面", "局部", "纹理", "一滴", "毛孔")
_WIDE_TOKENS = ("全景", "广角", "整间", "整个", "整座", "纵深", "两侧", "排列", "环境", "建立镜头")

# 空间归属：室内与室外不能共存于一帧
_INTERIOR_TOKENS = (
    "室内",
    "房间",
    "产房",
    "走廊",
    "客厅",
    "卧室",
    "车内",
    "舱内",
    "地下室",
    "病房",
)
_EXTERIOR_TOKENS = ("室外", "街道", "屋顶", "旷野", "山顶", "广场", "天空", "海边", "户外")

_BLACK_FRAME_TOKENS = ("黑场", "全黑画面", "画面全黑", "黑屏")

# 可见范围声明
_FACE_HIDDEN_TOKENS = ("不露脸", "不露出面部", "不露出脸", "不露出可识别", "看不到脸", "遮住脸")
_OFFSCREEN_TOKENS = ("不入镜", "不出现在画面", "不进入画面", "画外", "只有声音", "仅有声音")
_HANDS_ONLY_TOKENS = ("手套", "双手", "一双手", "手部", "指尖", "袖口", "手腕")
_BACK_ONLY_TOKENS = ("背影", "背对镜头", "背对着镜头", "从背后")

_CLAUSE_SPLIT = re.compile(r"[，,。；;、]")
_SENTENCE_END = re.compile(r"[。；;]")

# 画面可见范围枚举
VISIBILITY_OFF_SCREEN = "OFF_SCREEN"
VISIBILITY_HANDS_ONLY = "HANDS_ONLY"
VISIBILITY_BACK_ONLY = "BACK_ONLY"
VISIBILITY_FACE_VISIBLE = "FACE_VISIBLE"

RENDER_MODE_IMAGE = "IMAGE"
RENDER_MODE_BLACK_FRAME = "BLACK_FRAME"

SCALE_MACRO = "MACRO"
SCALE_WIDE = "WIDE"
SCALE_NEUTRAL = "NEUTRAL"

CONFLICT_BLOCKING = "BLOCKING"
CONFLICT_NORMALIZED = "NORMALIZED"


@dataclass(frozen=True)
class FrameBeat:
    """一个可以被单张静帧完整表达的画面时刻。"""

    visual: str
    render_mode: str = RENDER_MODE_IMAGE
    scale: str = SCALE_NEUTRAL
    audio_cues: tuple[str, ...] = ()
    camera_notes: tuple[str, ...] = ()
    timeline_notes: tuple[str, ...] = ()

    @property
    def shot_size_hint(self) -> str | None:
        """把画面尺度映射到既有景别词表（不新增枚举值）。"""
        if self.scale == SCALE_MACRO:
            return "CU"
        if self.scale == SCALE_WIDE:
            return "WS"
        return None

    @property
    def is_macro(self) -> bool:
        return self.scale == SCALE_MACRO


@dataclass(frozen=True)
class FrameSplit:
    """一段文案拆分后的全部画面时刻与整段生效的可见范围声明。"""

    beats: tuple[FrameBeat, ...]
    offscreen_names: tuple[str, ...] = ()
    face_hidden_names: tuple[str, ...] = ()
    story_context_removed: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrameConflict:
    """提交生图前发现的冲突。"""

    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class PromptDebugRemoval:
    """从生图 Prompt 中移除的字段及原因。"""

    field: str
    reason: str
    sample: str = ""


@dataclass(frozen=True)
class PromptDebug:
    """发送模型前的 Prompt 装配诊断。"""

    included: tuple[str, ...] = ()
    removed: tuple[PromptDebugRemoval, ...] = ()
    conflicts: tuple[FrameConflict, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "included": list(self.included),
            "removed": [
                {"field": item.field, "reason": item.reason, "sample": item.sample}
                for item in self.removed
            ],
            "conflicts": [
                {"code": item.code, "severity": item.severity, "message": item.message}
                for item in self.conflicts
            ],
        }


@dataclass
class _BeatDraft:
    clauses: list[str] = field(default_factory=list)
    audio_cues: list[str] = field(default_factory=list)
    camera_notes: list[str] = field(default_factory=list)
    timeline_notes: list[str] = field(default_factory=list)
    render_mode: str = RENDER_MODE_IMAGE
    forced_scale: str | None = None

    def is_empty(self) -> bool:
        return not self.clauses and not self.audio_cues


# ---------------------------------------------------------------------------
# 子句级分类
# ---------------------------------------------------------------------------


def _contains(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _is_black_frame_clause(clause: str) -> bool:
    return _contains(clause, _BLACK_FRAME_TOKENS)


def _is_audio_clause(clause: str) -> bool:
    """整句只描述听觉事件时返回 True。"""
    if _contains(clause, _INHERENT_AUDIO_TOKENS):
        return True
    if not _contains(clause, _AUDIO_NOUNS):
        return False
    # 声源名词 + 句尾听觉谓语 → 该句在描述声音而非画面
    return any(clause.rstrip().endswith(verb) for verb in _SOUND_VERBS)


def _is_narrative_clause(clause: str) -> bool:
    """整句在说明剧情作用而非画面内容时返回 True。"""
    return _contains(clause, _NARRATIVE_VERBS) and _contains(clause, _NARRATIVE_OBJECTS)


def _is_story_context_clause(clause: str) -> bool:
    """整句只承载世界观/法规/时间跨度/心理，不含可拍摄主体时返回 True。"""
    if _contains(clause, _STORY_CONTEXT_TOKENS):
        return True
    if _contains(clause, _PSYCHOLOGY_TOKENS):
        return True
    stripped = clause
    for pattern in _STORY_YEAR_PATTERNS:
        stripped = re.sub(pattern, "", stripped, flags=re.IGNORECASE)
    stripped = _normalize(stripped)
    # 去掉时间跨度后几乎为空 → 纯故事时间信息
    if not stripped and any(re.search(pattern, clause, flags=re.IGNORECASE) for pattern in _STORY_YEAR_PATTERNS):
        return True
    return False


def _clause_scale(clause: str) -> str:
    macro = _contains(clause, _MACRO_TOKENS)
    wide = _contains(clause, _WIDE_TOKENS)
    if macro and not wide:
        return SCALE_MACRO
    if wide and not macro:
        return SCALE_WIDE
    return SCALE_NEUTRAL


def _clause_space(clause: str) -> str:
    interior = _contains(clause, _INTERIOR_TOKENS)
    exterior = _contains(clause, _EXTERIOR_TOKENS)
    if interior and not exterior:
        return "INTERIOR"
    if exterior and not interior:
        return "EXTERIOR"
    return "NEUTRAL"


def _split_transition(clause: str) -> tuple[str | None, str]:
    """切出衔接用语，返回 (衔接短语, 剩余画面内容)。"""
    for pattern in _TRANSITION_PATTERNS:
        match = re.search(pattern, clause)
        if match is None:
            continue
        return match.group(0), (clause[: match.start()] + clause[match.end() :])
    return None, clause


def _strip_camera_movement(clause: str) -> tuple[str, list[str]]:
    """删除运镜过程用语，返回 (剩余内容, 被移出的运镜说明)。"""
    notes: list[str] = []
    result = clause
    # 长词优先，且「后拉」不得误伤「随后…」
    for token in sorted(_CAMERA_MOVE_TOKENS, key=len, reverse=True):
        if token == "后拉":
            pattern = r"(?<!随)后拉"
        else:
            pattern = re.escape(token)
        if re.search(pattern, result) is None:
            continue
        notes.append(token)
        result = re.sub(pattern, "", result)
    return result, notes


def _strip_timeline(clause: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    result = clause
    for pattern in _TIMELINE_PATTERNS:
        for match in re.findall(pattern, result):
            notes.append(match)
        result = re.sub(pattern, "", result)
    for token in sorted(_TIMELINE_TOKENS, key=len, reverse=True):
        if token not in result:
            continue
        notes.append(token)
        result = result.replace(token, "")
    return result, notes


def _strip_story_context(clause: str) -> tuple[str, list[str]]:
    """删除句中的世界观/时间跨度/心理碎片，保留可见主体。"""
    removed: list[str] = []
    result = clause
    for pattern in _STORY_YEAR_PATTERNS:
        for match in re.findall(pattern, result, flags=re.IGNORECASE):
            removed.append(match if isinstance(match, str) else match[0])
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    for token in sorted((*_STORY_CONTEXT_TOKENS, *_PSYCHOLOGY_TOKENS), key=len, reverse=True):
        if token not in result:
            continue
        removed.append(token)
        result = result.replace(token, "")
    return result, removed


def _is_pull_back_transition(transition: str | None) -> bool:
    if not transition:
        return False
    return _contains(transition, _PULL_BACK_TOKENS)


def _strip_embedded_audio(clause: str) -> tuple[str, list[str]]:
    """删除句中的听觉引用（如「随胎心」），保留可见主体。"""
    cues: list[str] = []
    result = clause
    audio_alternatives = "|".join(
        re.escape(item) for item in (*_AUDIO_NOUNS, *_INHERENT_AUDIO_TOKENS)
    )
    causal = re.compile(
        rf"(?:随着?|伴随着?|应着|跟着)[^，。；]{{0,4}}(?:{audio_alternatives})"
    )
    for match in causal.findall(result):
        cues.append(match)
    result = causal.sub("", result)
    # 残留的孤立声源名词一并移除，避免模型把声音画成字幕或波形
    for token in (*_INHERENT_AUDIO_TOKENS, *_AUDIO_NOUNS):
        if token not in result:
            continue
        cues.append(token)
        result = result.replace(token, "")
    return result, cues


def _strip_performance(clause: str) -> str:
    result = clause
    for pattern in _PERFORMANCE_PATTERNS:
        result = re.sub(pattern, "", result)
    return result


def _normalize(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text)
    cleaned = re.sub(r"[，,]{2,}", "，", cleaned)
    cleaned = re.sub(r"[。；;]{2,}", "。", cleaned)
    return cleaned.strip("，,。；;、的 ")


# 声明句里的限定词，不能当作角色名
_DIRECTIVE_MODIFIERS = ("全程", "始终", "一直", "完全", "仅有", "只有", "仅", "只", "有", "是")

VISIBILITY_FACE_HIDDEN = "FACE_HIDDEN"


def _directive_subject(clause: str) -> str:
    """取声明句的主语；以限定词开头说明该句是对上一条声明的补充。"""
    match = re.match(r"[\u4e00-\u9fa5A-Za-z]{2,8}", clause)
    if match is None:
        return ""
    head = match.group(0)
    for modifier in _DIRECTIVE_MODIFIERS:
        if head.startswith(modifier):
            return ""
        head = head.split(modifier)[0]
    return head if len(head) >= 2 else ""


def _visibility_directive(clause: str) -> tuple[str, str] | None:
    """识别「某角色不露脸 / 仅有画外音」这类可见范围声明。"""
    face_hidden = _contains(clause, _FACE_HIDDEN_TOKENS)
    offscreen = _contains(clause, _OFFSCREEN_TOKENS) or _contains(
        clause, _INHERENT_AUDIO_TOKENS
    )
    if not face_hidden and not offscreen:
        return None
    mode = VISIBILITY_OFF_SCREEN if offscreen else VISIBILITY_FACE_HIDDEN
    # 「医生画外音」先去掉音频词再取主语，避免把「画外音」并进角色名
    subject_source = clause
    for token in (*_INHERENT_AUDIO_TOKENS, *_OFFSCREEN_TOKENS, *_FACE_HIDDEN_TOKENS):
        subject_source = subject_source.replace(token, "")
    subject_source = _normalize(subject_source)
    return _directive_subject(subject_source) or _directive_subject(clause), mode


def _strip_patterns_list(text: str, tokens: tuple[str, ...]) -> str:
    result = text
    for token in tokens:
        result = result.replace(token, "")
    return result


# ---------------------------------------------------------------------------
# 拆帧主流程
# ---------------------------------------------------------------------------


def split_visual_moments(description: str) -> FrameSplit:
    """把一段镜头描述拆成互不冲突的单帧时刻。"""
    text = (description or "").strip()
    if not text:
        return FrameSplit(beats=())
    text = re.sub(r"^画外音覆盖于[：:]", "", text).strip()

    drafts: list[_BeatDraft] = []
    current = _BeatDraft()
    current_scale = SCALE_NEUTRAL
    current_space = "NEUTRAL"
    offscreen: list[str] = []
    face_hidden: list[str] = []
    last_directive_subject = ""
    pending_camera: list[str] = []
    pending_timeline: list[str] = []
    story_context_removed: list[str] = []

    def close_current() -> None:
        nonlocal current, current_scale, current_space
        if not current.is_empty():
            drafts.append(current)
        current = _BeatDraft()
        current_scale = SCALE_NEUTRAL
        current_space = "NEUTRAL"

    def spill_non_macro_to_next(transition: str | None) -> list[str]:
        """拉远/拉焦露出时，把前一拍里的空间/广角子句挪到下一拍。"""
        nonlocal current_scale
        if not _is_pull_back_transition(transition):
            return []
        if not current.clauses:
            return []
        has_macro = any(_clause_scale(item) == SCALE_MACRO for item in current.clauses)
        if not has_macro:
            return []
        kept: list[str] = []
        spilled: list[str] = []
        for item in current.clauses:
            scale = _clause_scale(item)
            if scale == SCALE_MACRO:
                kept.append(item)
            elif scale == SCALE_WIDE or _clause_space(item) != "NEUTRAL":
                spilled.append(item)
            elif _contains(item, _INTERIOR_TOKENS) or _contains(item, _EXTERIOR_TOKENS):
                # 中性但与微距并存的空间名（如「废弃产房」）随拉远进入下一拍
                spilled.append(item)
            else:
                kept.append(item)
        if not spilled or not kept:
            return []
        current.clauses = kept
        current_scale = _beat_scale(kept)
        return spilled

    for raw_clause in _CLAUSE_SPLIT.split(text):
        clause = raw_clause.strip()
        if not clause:
            continue

        directive = _visibility_directive(clause)
        if directive is not None:
            subject, mode = directive
            if _contains(clause, _INHERENT_AUDIO_TOKENS):
                current.audio_cues.append(clause)
            # 无主语的补充声明（如「仅有平静画外音」）作用于上一条声明的角色
            subject = subject or last_directive_subject
            if subject:
                last_directive_subject = subject
                if mode == VISIBILITY_OFF_SCREEN:
                    offscreen.append(subject)
                    if subject in face_hidden:
                        face_hidden.remove(subject)
                elif subject not in offscreen:
                    face_hidden.append(subject)
            continue

        if _is_black_frame_clause(clause):
            close_current()
            current.render_mode = RENDER_MODE_BLACK_FRAME
            current.clauses.append(_normalize(clause))
            continue

        if _is_audio_clause(clause):
            current.audio_cues.append(clause)
            continue

        if _is_narrative_clause(clause) or _is_story_context_clause(clause):
            story_context_removed.append(clause)
            continue

        transition, residual = _split_transition(clause)
        residual, camera_notes = _strip_camera_movement(residual)
        residual, timeline_notes = _strip_timeline(residual)
        residual, embedded_audio = _strip_embedded_audio(residual)
        residual, story_bits = _strip_story_context(residual)
        story_context_removed.extend(story_bits)
        residual = _normalize(_strip_performance(residual))

        if transition is not None:
            camera_notes.insert(0, transition)

        clause_scale = _clause_scale(residual or clause)
        clause_space = _clause_space(residual or clause)
        needs_new_beat = (
            transition is not None
            or current.render_mode == RENDER_MODE_BLACK_FRAME
            or (
                clause_scale != SCALE_NEUTRAL
                and current_scale != SCALE_NEUTRAL
                and clause_scale != current_scale
            )
            or (
                clause_space != "NEUTRAL"
                and current_space != "NEUTRAL"
                and clause_space != current_space
            )
        )
        spilled: list[str] = []
        if needs_new_beat and not current.is_empty():
            spilled = spill_non_macro_to_next(transition)
            close_current()
            current.camera_notes.extend(pending_camera)
            current.timeline_notes.extend(pending_timeline)
            pending_camera = []
            pending_timeline = []
            current.clauses.extend(spilled)
            if spilled:
                current.forced_scale = SCALE_WIDE
                current_scale = SCALE_WIDE
                for item in spilled:
                    space = _clause_space(item)
                    if space != "NEUTRAL":
                        current_space = space

        current.camera_notes.extend(camera_notes)
        current.timeline_notes.extend(timeline_notes)
        current.audio_cues.extend(embedded_audio)
        if residual:
            current.clauses.append(residual)
            if clause_scale != SCALE_NEUTRAL:
                current_scale = clause_scale
            if clause_space != "NEUTRAL":
                current_space = clause_space

    close_current()

    beats = tuple(
        FrameBeat(
            visual=_normalize("，".join(draft.clauses)),
            render_mode=draft.render_mode,
            scale=draft.forced_scale or _beat_scale(draft.clauses),
            audio_cues=_dedupe(draft.audio_cues),
            camera_notes=_dedupe(draft.camera_notes),
            timeline_notes=_dedupe(draft.timeline_notes),
        )
        for draft in drafts
    )
    beats = tuple(
        beat
        for beat in beats
        if beat.visual or beat.render_mode == RENDER_MODE_BLACK_FRAME or beat.audio_cues
    )
    return FrameSplit(
        beats=beats,
        offscreen_names=_dedupe(offscreen),
        face_hidden_names=_dedupe(face_hidden),
        story_context_removed=_dedupe(story_context_removed),
    )


def _beat_scale(clauses: list[str]) -> str:
    joined = "".join(clauses)
    return _clause_scale(joined)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return tuple(seen)


def collapse_to_single_beat(split: FrameSplit, *, shot_size: str) -> FrameBeat | None:
    """只出一帧时选与景别最匹配的时刻，并把其余时刻的声音/运镜信息并入，避免丢失。"""
    if not split.beats:
        return None
    size = (shot_size or "").upper()
    renderable = [beat for beat in split.beats if beat.render_mode == RENDER_MODE_IMAGE]
    chosen = next(
        (beat for beat in renderable if beat.shot_size_hint == size),
        next((beat for beat in renderable if beat.visual), split.beats[0]),
    )
    return FrameBeat(
        visual=chosen.visual,
        render_mode=chosen.render_mode,
        scale=chosen.scale,
        audio_cues=_dedupe([cue for beat in split.beats for cue in beat.audio_cues]),
        camera_notes=_dedupe([note for beat in split.beats for note in beat.camera_notes]),
        timeline_notes=_dedupe(
            [note for beat in split.beats for note in beat.timeline_notes]
        ),
    )


def compile_static_frame_brief(description: str, *, shot_size: str) -> str:
    """取与当前景别最匹配的单帧描述，供既有单帧提示词复用。"""
    beat = collapse_to_single_beat(split_visual_moments(description), shot_size=shot_size)
    return beat.visual if beat is not None else ""


# ---------------------------------------------------------------------------
# 可见范围
# ---------------------------------------------------------------------------


def resolve_visibility(
    *,
    character_name: str,
    visual_brief: str,
    frame_text: str,
    delivery: str,
    offscreen_names: tuple[str, ...] = (),
    face_hidden_names: tuple[str, ...] = (),
) -> str:
    """判定角色在当前画面里的可见范围，决定该传哪类参考图。

    角色 brief 里的「仅出手/不露脸」是出镜约束，不是每帧强制入镜；
    只有本帧画面出现手/背等证据时，才判定为局部可见。
    """
    brief = visual_brief or ""
    named_in_frame = bool(character_name) and character_name in frame_text
    hands_in_frame = _contains(frame_text, _HANDS_ONLY_TOKENS)
    back_in_frame = _contains(frame_text, _BACK_ONLY_TOKENS)

    if any(character_name and character_name in item for item in offscreen_names):
        return VISIBILITY_OFF_SCREEN
    if delivery == "VOICE_OVER" and not named_in_frame:
        return VISIBILITY_OFF_SCREEN

    declared_face_hidden = any(
        character_name and character_name in item for item in face_hidden_names
    ) or _contains(brief, _FACE_HIDDEN_TOKENS)

    # 本帧有手部/背影证据时，按局部可见处理
    if hands_in_frame and (declared_face_hidden or not named_in_frame):
        return VISIBILITY_HANDS_ONLY
    if back_in_frame:
        return VISIBILITY_BACK_ONLY

    if declared_face_hidden:
        # brief「仅出手」不能让空镜也绑上手部角色
        return VISIBILITY_OFF_SCREEN
    return VISIBILITY_FACE_VISIBLE


def visibility_prompt_clause(name: str, role: str, brief: str, visibility: str) -> str:
    """按可见范围生成角色约束文案，只描述当前画面能看到的部分。"""
    # 角色 brief 中的声音描述不进入生图约束
    visual_brief = brief or ""
    for token in (*_INHERENT_AUDIO_TOKENS, *_AUDIO_NOUNS):
        visual_brief = visual_brief.replace(token, "")
    visual_brief = _normalize(visual_brief) or "沿用锁定可见特征"

    if visibility == VISIBILITY_OFF_SCREEN:
        return f"- {name}（{role}）本帧不入镜，画面中不得出现其身体、手部或面部。"
    if visibility == VISIBILITY_HANDS_ONLY:
        return (
            f"- {name}（{role}）本帧只出现手部与袖口：{visual_brief}。"
            "不出现面部、颈部与完整身形，禁止用口罩或侧脸折中露出五官。"
        )
    if visibility == VISIBILITY_BACK_ONLY:
        return (
            f"- {name}（{role}）本帧只出现背影：{visual_brief}。"
            "以体态、发型轮廓与服装辨识，禁止转头露出可识别五官。"
        )
    return f"- 参考图对应角色：{name}（{role}）；{visual_brief}"


# ---------------------------------------------------------------------------
# 冲突检查
# ---------------------------------------------------------------------------

_SHALLOW_DEPTH_TOKENS = ("浅景深", "大光圈", "焦外", "背景虚化")
_DEEP_FOCUS_TOKENS = ("整个空间全部清晰", "全景深", "处处清晰", "前后都清晰")
_SYMMETRY_TOKENS = ("对称构图", "完全对称", "正面对称")
_ASYMMETRY_TOKENS = ("非对称构图", "非对称", "不对称构图")
_FACE_DETAIL_TOKENS = ("眼神", "五官", "毛孔", "表情", "唇形", "瞳距")
_PERFORMANCE_REQUIREMENT_TOKENS = ("表演", "完成台词", "口型", "眼神", "表情")
_POSITION_TOKENS = ("画面左", "画面右", "画面中央", "居中", "靠左", "靠右")


def detect_frame_conflicts(
    *,
    frame_text: str,
    shot_size: str,
    camera_movement: str,
    visible_character_count: int,
    face_visible_count: int,
    identity_reference_count: int,
) -> list[FrameConflict]:
    """检查明显冲突；BLOCKING 表示必须人工拆镜或澄清叙事意图。"""
    conflicts: list[FrameConflict] = []
    text = frame_text or ""
    size = (shot_size or "").upper()

    macro = _contains(text, _MACRO_TOKENS)
    wide = _contains(text, _WIDE_TOKENS)
    if macro and wide:
        conflicts.append(
            FrameConflict(
                code="SCALE_MACRO_AND_WIDE",
                severity=CONFLICT_BLOCKING,
                message="同一帧同时要求极微距与全景，必须拆成两个镜头。",
            )
        )
    if size == "WS" and macro and not wide:
        conflicts.append(
            FrameConflict(
                code="SHOT_SIZE_MISMATCH",
                severity=CONFLICT_NORMALIZED,
                message="景别标记为 WS 但画面只描述微距细节，已按画面内容出图。",
            )
        )

    if _contains(text, _BLACK_FRAME_TOKENS) and _normalize(
        _strip_patterns_list(text, _BLACK_FRAME_TOKENS)
    ):
        conflicts.append(
            FrameConflict(
                code="BLACK_FRAME_WITH_CONTENT",
                severity=CONFLICT_BLOCKING,
                message="黑场与实体画面不能合成一张图，必须拆成两个镜头。",
            )
        )

    if _clause_space(text) == "NEUTRAL" and _contains(text, _INTERIOR_TOKENS) and _contains(
        text, _EXTERIOR_TOKENS
    ):
        conflicts.append(
            FrameConflict(
                code="INTERIOR_AND_EXTERIOR",
                severity=CONFLICT_BLOCKING,
                message="同一帧同时要求室内与室外，必须拆成两个镜头。",
            )
        )

    if (camera_movement or "").upper() == "STATIC" and _contains(text, _CAMERA_MOVE_TOKENS):
        conflicts.append(
            FrameConflict(
                code="STATIC_WITH_CAMERA_MOVE",
                severity=CONFLICT_NORMALIZED,
                message="固定机位单帧不能表现推拉摇移，运镜信息已移入镜头字段。",
            )
        )

    if face_visible_count == 0 and _contains(text, _FACE_DETAIL_TOKENS):
        conflicts.append(
            FrameConflict(
                code="HIDDEN_FACE_WITH_FACE_DETAIL",
                severity=CONFLICT_NORMALIZED,
                message="本帧无露脸人物，已移除五官、眼神与皮肤细节要求。",
            )
        )

    if visible_character_count == 0 and _contains(text, _PERFORMANCE_REQUIREMENT_TOKENS):
        conflicts.append(
            FrameConflict(
                code="NO_CAST_WITH_PERFORMANCE",
                severity=CONFLICT_NORMALIZED,
                message="本帧没有可见人物，已移除人物表演要求。",
            )
        )

    if _contains(text, _SYMMETRY_TOKENS) and _contains(text, _ASYMMETRY_TOKENS):
        conflicts.append(
            FrameConflict(
                code="SYMMETRY_CONTRADICTION",
                severity=CONFLICT_NORMALIZED,
                message="对称与非对称构图冲突，已采用画面明确指定的构图。",
            )
        )

    if _contains(text, _SHALLOW_DEPTH_TOKENS) and _contains(text, _DEEP_FOCUS_TOKENS):
        conflicts.append(
            FrameConflict(
                code="DEPTH_OF_FIELD_CONTRADICTION",
                severity=CONFLICT_NORMALIZED,
                message="浅景深与全空间清晰冲突，已采用浅景深。",
            )
        )

    # 仅有画外音/完全不入镜的角色不应带来任何身份参考图
    if visible_character_count == 0 and identity_reference_count > 0:
        conflicts.append(
            FrameConflict(
                code="OFFSCREEN_CAST_WITH_IDENTITY_REFERENCE",
                severity=CONFLICT_NORMALIZED,
                message="本帧无人物出镜，已移除角色身份参考图。",
            )
        )

    conflicts.extend(_position_conflicts(text))
    return conflicts


def _position_conflicts(text: str) -> list[FrameConflict]:
    """同一主体被要求出现在互斥位置时阻止提交。"""
    hits = [token for token in _POSITION_TOKENS if token in text]
    left = any(token in hits for token in ("画面左", "靠左"))
    right = any(token in hits for token in ("画面右", "靠右"))
    centre = any(token in hits for token in ("画面中央", "居中"))
    if sum((left, right, centre)) < 2:
        return []
    subjects = re.findall(r"([\u4e00-\u9fa5]{2,6})(?:同时)?(?:位于|站在|处于)", text)
    if len(set(subjects)) == 1 and subjects:
        return [
            FrameConflict(
                code="SUBJECT_POSITION_CONTRADICTION",
                severity=CONFLICT_BLOCKING,
                message=f"主体「{subjects[0]}」被要求同时出现在互斥位置，需要人工澄清。",
            )
        ]
    return []


def blocking_conflicts(conflicts: list[FrameConflict]) -> list[FrameConflict]:
    return [item for item in conflicts if item.severity == CONFLICT_BLOCKING]
