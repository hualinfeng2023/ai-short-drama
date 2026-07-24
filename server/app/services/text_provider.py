import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.config import Settings
from app.domain.narrative_targeting import (
    EmotionalReward,
    NarrativeProtagonist,
    ProductionFormat,
    TargetAudience,
    reject_unrequested_stereotypes,
    targeting_from_brief,
    targeting_prompt_guardrails,
)
from app.schemas import (
    RelationshipGraphPayload,
    RelationshipGraphValidationIssue,
    RelationshipStatePayload,
)
from app.services.relationship_graphs import (
    relationship_graph_has_blockers,
    validate_relationship_graph,
)

# 结构化创作合同的完整 JSON 输出普遍超过默认输出上限，显式提高上限防止 JSON 被截断
ARK_TEXT_MAX_OUTPUT_TOKENS = 16_384

SHORT_DRAMA_FORMULA_VERSION = "short-drama-v1"
SHORT_DRAMA_FORMULA = (
    "明确的角色欲望 × 高密度推进 × 高频情绪兑现 × 递进式因果反转 × 阶段性闭环与续作悬念"
)
BREAKOUT_FORMULA_VERSION = "breakout-drama-v1"
BREAKOUT_FORMULA = (
    "弱势外壳 × 顶级内核 × 持续误判 × 分段认证 × 关系重排 × 情感秩序重建 × 可续作单元"
)


class TextProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}


class ModelOutputSemanticError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        repair_message: str,
        details: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.repair_message = repair_message
        self.details = details


class DirectionShot(BaseModel):
    code: str
    duration_sec: int = Field(ge=1, le=20)
    shot_size: Literal["WS", "MS", "MCU", "CU"]
    camera: Literal["STATIC", "PAN", "DOLLY_IN", "TRACK", "HANDHELD"]


class DirectionScene(BaseModel):
    code: str
    title: str
    purpose: str
    duration_sec: int = Field(ge=1, le=60)
    shots: list[DirectionShot] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_duration(self) -> "DirectionScene":
        if sum(item.duration_sec for item in self.shots) != self.duration_sec:
            raise ValueError("场景时长必须等于镜头时长之和")
        return self


def _reject_question_hook(value: str) -> str:
    normalized = value.strip()
    forbidden = ("?", "？", "你会", "是否", "评论区", "你怎么选", "你如何选")
    if any(token in normalized for token in forbidden):
        raise ValueError("续作钩子必须是具体剧情铺垫，不能是观众提问或互动 CTA")
    return normalized


class SequelSetup(BaseModel):
    current_arc_closure: str
    final_reveal_or_action: str
    next_installment_conflict: str
    next_installment_objective: str

    @field_validator(
        "final_reveal_or_action",
        "next_installment_conflict",
        "next_installment_objective",
    )
    @classmethod
    def validate_plot_statement(cls, value: str) -> str:
        return _reject_question_hook(value)


class StoryDna(BaseModel):
    core_premise: str
    protagonist_want: str
    protagonist_need: str
    central_conflict: str
    stakes: str
    emotional_promise: str
    payoff: str
    ending_hook: str
    tone_keywords: list[str] = Field(min_length=3, max_length=8)

    @field_validator("ending_hook")
    @classmethod
    def validate_ending_hook(cls, value: str) -> str:
        return _reject_question_hook(value)


class BriefComplianceItem(BaseModel):
    category: Literal["REQUIREMENT", "AVOIDANCE"]
    item: str
    status: Literal["MET", "PARTIAL", "CONFLICT"]
    evidence: str


class BriefCompliance(BaseModel):
    status: Literal["ALL_MET", "PARTIAL", "CONFLICT"]
    items: list[BriefComplianceItem]


class EstimatedGenerationScale(BaseModel):
    keyframe_images: int = Field(ge=1)
    video_clips: int = Field(ge=1)
    voice_segments: int = Field(ge=1)


class ProductionComplexity(BaseModel):
    character_count: int = Field(ge=1, le=30)
    scene_count: int = Field(ge=1, le=30)
    exterior_scene_count: int = Field(ge=0, le=30)
    exterior_requirements: list[str] = Field(max_length=8)
    vfx_requirements: list[str] = Field(max_length=8)
    estimated_generation: EstimatedGenerationScale


class FirstEpisodeRhythm(BaseModel):
    opening_3s_hook: str
    first_payoff: str
    ending_action: str

    @field_validator("ending_action")
    @classmethod
    def validate_ending_action(cls, value: str) -> str:
        return _reject_question_hook(value)


class AiRecommendation(BaseModel):
    recommended: bool
    brief_matches: list[str] = Field(min_length=1, max_length=5)
    reason: str


class NarrativeTargetingContract(BaseModel):
    narrative_protagonist: NarrativeProtagonist
    target_audience: TargetAudience
    emotional_rewards: list[EmotionalReward] = Field(min_length=1, max_length=7)
    audience_profile: str = Field(default="", max_length=240)
    production_format: ProductionFormat


def _validate_targeting_contract(
    payload: dict[str, Any], brief: dict[str, Any], *, nested_key: str | None = None
) -> None:
    source = payload.get(nested_key, {}) if nested_key else payload
    if not isinstance(source, dict):
        raise ValueError("生成结果缺少叙事定位合同")
    generated = NarrativeTargetingContract.model_validate(source.get("narrative_targeting"))
    expected = NarrativeTargetingContract.model_validate(targeting_from_brief(brief))
    if generated != expected:
        raise ValueError("生成结果改写了项目简报中的独立叙事定位")
    reject_unrequested_stereotypes(payload, brief)


def _enforce_generated_targeting(
    payload: dict[str, Any],
    brief: dict[str, Any],
    *,
    nested_key: str | None = None,
    require_contract: bool = True,
) -> None:
    try:
        if require_contract:
            _validate_targeting_contract(payload, brief, nested_key=nested_key)
        else:
            reject_unrequested_stereotypes(payload, brief)
    except (ValueError, ValidationError) as exc:
        raise TextProviderError(
            "NARRATIVE_TARGETING_INVALID",
            str(exc),
            retryable=False,
        ) from exc


class StoryDirection(BaseModel):
    narrative_targeting: NarrativeTargetingContract
    direction_key: str
    title: str
    logline: str
    director_statement: str
    differentiator: str
    audience_fit: str
    visual_signature: str
    selection_tradeoff: str
    key_turns: list[str] = Field(min_length=3, max_length=5)
    risk_notes: list[str] = Field(min_length=1, max_length=3)
    sequel_setup: SequelSetup
    total_duration_sec: int = Field(ge=45, le=90)
    scenes: list[DirectionScene] = Field(min_length=2, max_length=8)
    assumptions: list[str]
    story_dna: StoryDna
    brief_compliance: BriefCompliance
    production_complexity: ProductionComplexity
    first_episode_rhythm: FirstEpisodeRhythm
    ai_recommendation: AiRecommendation

    @model_validator(mode="after")
    def validate_duration(self) -> "StoryDirection":
        if sum(item.duration_sec for item in self.scenes) != self.total_duration_sec:
            raise ValueError("方向总时长必须等于场景时长之和")
        if self.production_complexity.scene_count != len(self.scenes):
            raise ValueError("制作复杂度的场景数必须与方向场景一致")
        if self.production_complexity.exterior_scene_count > len(self.scenes):
            raise ValueError("外景数不能超过总场景数")
        return self


class StoryDirectionBatch(BaseModel):
    directions: list[StoryDirection] = Field(min_length=3, max_length=3)

    @field_validator("directions")
    @classmethod
    def unique_directions(cls, values: list[StoryDirection]) -> list[StoryDirection]:
        if len({item.direction_key for item in values}) != 3:
            raise ValueError("三个故事方向必须具有不同 direction_key")
        if len({item.differentiator for item in values}) != 3:
            raise ValueError("三个故事方向必须明确差异化")
        if sum(item.ai_recommendation.recommended for item in values) != 1:
            raise ValueError("三个故事方向中必须且只能推荐一个")
        return values


DIRECTION_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("emotion", "情绪兑现", "突出人物关系、情感选择与有因果支撑的余韵反转"),
    ("plot", "强情节", "突出限时任务、可验证线索与连续改变局势的反转"),
    ("market", "市场钩子", "突出身份翻盘、即时爽点与目标市场传播钩子"),
)

EMOTIONAL_REWARD_LABELS: dict[str, str] = {
    "romance": "爱情",
    "identity": "身份",
    "career": "事业",
    "revenge": "复仇",
    "family": "亲情",
    "power": "权力",
    "public_mission": "公共使命",
}
TARGET_AUDIENCE_LABELS = {
    "male_frequency": "男频",
    "female_frequency": "女频",
    "general": "泛人群",
}


class BibleCharacter(BaseModel):
    key: str
    name: str
    role: str
    gender: Literal["male", "female", "nonbinary", "unspecified"] = "unspecified"
    ethnicity: str = "unspecified"
    age: str
    height: str = "未指定"
    occupation: str
    personality: list[str] = Field(min_length=1, max_length=5)
    dramatic_function: str
    desire: str
    fear: str
    secret: str
    visual_notes: str


class StoryBible(BaseModel):
    narrative_targeting: NarrativeTargetingContract
    world: str
    rules: list[str]
    characters: list[BibleCharacter] = Field(min_length=1)
    relationships: list[str]
    foreshadowing: list[str]
    continuity_rules: list[str]


class StoryBibleV2(BaseModel):
    narrative_targeting: NarrativeTargetingContract
    world: str
    rules: list[str]
    characters: list[BibleCharacter] = Field(min_length=1)
    foreshadowing: list[str]
    continuity_rules: list[str]


class EpisodeOutline(BaseModel):
    episode_ordinal: int = Field(ge=1)
    title: str
    hook: str
    objective: str
    conflict: str
    turn: str
    cliffhanger: str
    target_duration_sec: int = Field(ge=45, le=90)


class ScriptLinePayload(BaseModel):
    speaker_key: str
    text: str = Field(min_length=1)
    line_type: Literal["DIALOGUE", "VOICE_OVER", "ACTION"]
    emotion: str
    speech_rate: float = Field(ge=0.7, le=1.4)
    pause_after_ms: int = Field(ge=0, le=3000)
    estimated_duration_ms: int = Field(ge=200, le=20_000)
    pronunciation: dict[str, str] = Field(default_factory=dict)
    localizations: dict[str, str] = Field(default_factory=dict)


class ScriptScenePayload(BaseModel):
    heading: str
    location: str
    time_of_day: str
    purpose: str
    emotion: str
    duration_ms: int = Field(ge=1000, le=60_000)
    bgm_intent: str
    sfx_intents: list[str]
    lines: list[ScriptLinePayload] = Field(min_length=1)


class ShortDramaBeat(BaseModel):
    sequence: int = Field(ge=1)
    scene_ordinal: int = Field(ge=1)
    beat_type: Literal[
        "HOOK",
        "ESCALATION",
        "PAYOFF",
        "REVERSAL",
        "CLOSURE",
        "CONTINUATION_HOOK",
    ]
    at_ms: int = Field(ge=0, le=90_000)
    description: str = Field(min_length=1)
    story_state_change: str = Field(min_length=1)


class ShortDramaEngine(BaseModel):
    formula_version: Literal["short-drama-v1"] = SHORT_DRAMA_FORMULA_VERSION
    formula: str = Field(min_length=1)
    protagonist_desire: str = Field(min_length=1)
    pace_strategy: str = Field(min_length=1)
    payoff_strategy: str = Field(min_length=1)
    reversal_chain: list[str] = Field(min_length=2, max_length=6)
    stage_closure: str = Field(min_length=1)
    continuation_hook: str = Field(min_length=1)
    beats: list[ShortDramaBeat] = Field(min_length=7, max_length=16)

    @model_validator(mode="after")
    def validate_short_drama_contract(self) -> "ShortDramaEngine":
        sequences = [item.sequence for item in self.beats]
        if sequences != list(range(1, len(self.beats) + 1)):
            raise ValueError("短剧节拍 sequence 必须从 1 连续递增")
        beat_types = [item.beat_type for item in self.beats]
        for required in ("HOOK", "ESCALATION", "PAYOFF", "CLOSURE", "CONTINUATION_HOOK"):
            if required not in beat_types:
                raise ValueError(f"短剧节拍缺少 {required}")
        if beat_types.count("REVERSAL") < 2:
            raise ValueError("短剧至少需要两级递进式反转")
        if beat_types[-1] != "CONTINUATION_HOOK":
            raise ValueError("最后一个节拍必须是续作悬念")
        if beat_types.index("CLOSURE") >= len(beat_types) - 1:
            raise ValueError("阶段闭环必须发生在续作悬念之前")
        return self


class MisjudgmentStep(BaseModel):
    sequence: int = Field(ge=1)
    scene_ordinal: int = Field(ge=1)
    observer_key: str = Field(min_length=1)
    mistaken_belief: str = Field(min_length=1)
    resulting_action: str = Field(min_length=1)
    cost_to_protagonist: str = Field(min_length=1)
    correction_seed: str = Field(min_length=1)


class AuthenticationStage(BaseModel):
    sequence: int = Field(ge=1)
    scene_ordinal: int = Field(ge=1)
    proof_type: Literal["ABILITY", "IDENTITY", "MORALITY", "RESOURCE", "TRUTH"]
    proof: str = Field(min_length=1)
    reveals: str = Field(min_length=1)
    who_updates_belief: list[str] = Field(min_length=1)
    status_shift: str = Field(min_length=1)
    remaining_misjudgment: str = Field(min_length=1)


class RelationshipReorder(BaseModel):
    relationship_key: str = Field(min_length=1)
    before: str = Field(min_length=1)
    trigger_auth_sequence: int = Field(ge=1)
    after: str = Field(min_length=1)
    emotional_consequence: str = Field(min_length=1)
    source_character_key: str | None = None
    target_character_key: str | None = None
    before_state: RelationshipStatePayload | None = None
    after_state: RelationshipStatePayload | None = None
    relationship_beat_id: str | None = None


class EmotionalOrderRebuild(BaseModel):
    old_order: str = Field(min_length=1)
    rupture: str = Field(min_length=1)
    new_order: str = Field(min_length=1)
    emotional_payoff: str = Field(min_length=1)


class SequelUnit(BaseModel):
    current_unit_closure: str = Field(min_length=1)
    unresolved_engine: str = Field(min_length=1)
    next_unit_trigger: str = Field(min_length=1)
    escalation_promise: str = Field(min_length=1)

    @field_validator("next_unit_trigger")
    @classmethod
    def validate_next_unit_trigger(cls, value: str) -> str:
        return _reject_question_hook(value)


class BreakoutNarrativeEngine(BaseModel):
    formula_version: Literal["breakout-drama-v1"] = BREAKOUT_FORMULA_VERSION
    formula: str = Field(min_length=1)
    vulnerable_shell: str = Field(min_length=1)
    elite_core: str = Field(min_length=1)
    misjudgment_chain: list[MisjudgmentStep] = Field(min_length=2, max_length=8)
    authentication_ladder: list[AuthenticationStage] = Field(min_length=2, max_length=8)
    relationship_reorders: list[RelationshipReorder] = Field(min_length=1, max_length=8)
    emotional_order_rebuild: EmotionalOrderRebuild
    sequel_unit: SequelUnit

    @model_validator(mode="after")
    def validate_breakout_contract(self) -> "BreakoutNarrativeEngine":
        misjudgment_sequences = [item.sequence for item in self.misjudgment_chain]
        if misjudgment_sequences != list(range(1, len(self.misjudgment_chain) + 1)):
            raise ValueError("持续误判 sequence 必须从 1 连续递增")
        authentication_sequences = [item.sequence for item in self.authentication_ladder]
        if authentication_sequences != list(range(1, len(self.authentication_ladder) + 1)):
            raise ValueError("分段认证 sequence 必须从 1 连续递增")
        valid_authentication_sequences = set(authentication_sequences)
        if any(
            item.trigger_auth_sequence not in valid_authentication_sequences
            for item in self.relationship_reorders
        ):
            raise ValueError("关系重排必须引用有效的分段认证")
        return self


class EpisodeScript(BaseModel):
    episode_ordinal: int = Field(ge=1)
    title: str
    canonical_language: str
    estimated_duration_ms: int = Field(ge=45_000, le=90_000)
    short_drama_engine: ShortDramaEngine
    breakout_engine: BreakoutNarrativeEngine
    scenes: list[ScriptScenePayload] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def validate_duration(self) -> "EpisodeScript":
        scene_total = sum(item.duration_ms for item in self.scenes)
        if scene_total != self.estimated_duration_ms:
            raise ValueError("剧本总时长必须等于场景时长之和")
        beats = self.short_drama_engine.beats
        if [item.at_ms for item in beats] != sorted(item.at_ms for item in beats):
            raise ValueError("短剧节拍时间必须递增")
        if any(item.at_ms >= self.estimated_duration_ms for item in beats):
            raise ValueError("短剧节拍时间必须位于剧本时长内")
        if any(item.scene_ordinal > len(self.scenes) for item in beats):
            raise ValueError("短剧节拍引用了不存在的场景")
        breakout = self.breakout_engine
        if any(
            item.scene_ordinal > len(self.scenes)
            for item in [*breakout.misjudgment_chain, *breakout.authentication_ladder]
        ):
            raise ValueError("爆款叙事链引用了不存在的场景")
        authentication_scenes = [item.scene_ordinal for item in breakout.authentication_ladder]
        if authentication_scenes != sorted(authentication_scenes):
            raise ValueError("分段认证必须按场景顺序递进")
        if breakout.sequel_unit.current_unit_closure != self.short_drama_engine.stage_closure:
            raise ValueError("爆款叙事单元闭环必须与短剧阶段闭环一致")
        if breakout.sequel_unit.next_unit_trigger != self.short_drama_engine.continuation_hook:
            raise ValueError("可续作单元触发必须与短剧续作钩子一致")
        return self


class StoryPackage(BaseModel):
    story_bible: StoryBible
    outlines: list[EpisodeOutline] = Field(min_length=1, max_length=12)
    scripts: list[EpisodeScript] = Field(min_length=1, max_length=12)
    critic: dict[str, Any]

    @model_validator(mode="after")
    def validate_episode_coverage(self) -> "StoryPackage":
        outline_ids = [item.episode_ordinal for item in self.outlines]
        script_ids = [item.episode_ordinal for item in self.scripts]
        if len(outline_ids) != len(set(outline_ids)) or len(script_ids) != len(set(script_ids)):
            raise ValueError("分集编号不能重复")
        if script_ids[0] != 1 or 1 not in outline_ids:
            raise ValueError("首集 Outline 与 Script 必须存在")
        return self


class StoryFoundation(BaseModel):
    story_bible: StoryBible
    outlines: list[EpisodeOutline] = Field(min_length=1, max_length=12)

    @field_validator("outlines")
    @classmethod
    def validate_outline_coverage(cls, values: list[EpisodeOutline]) -> list[EpisodeOutline]:
        ordinals = [item.episode_ordinal for item in values]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("分集编号不能重复")
        if 1 not in ordinals:
            raise ValueError("首集 Outline 必须存在")
        return values


class StoryStructure(BaseModel):
    story_bible: StoryBibleV2
    relationship_graph: RelationshipGraphPayload
    critic: dict[str, Any]


def normalize_story_structure_payload(
    payload: dict[str, Any], brief: dict[str, Any]
) -> dict[str, Any]:
    """Restore server-owned fields and repair mechanical relationship invariants."""
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    story_bible = normalized.get("story_bible")
    if isinstance(story_bible, dict):
        story_bible["narrative_targeting"] = targeting_from_brief(brief)

    graph = normalized.get("relationship_graph")
    if not isinstance(graph, dict):
        return normalized

    edges = graph.get("edges")
    if isinstance(edges, list):
        valid_edges = [edge for edge in edges if isinstance(edge, dict)]
        deduplicated_edges: list[dict[str, Any]] = []
        canonical_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        relationship_key_aliases: dict[str, str] = {}
        for edge in valid_edges:
            source = str(edge.get("source_character_key", ""))
            target = str(edge.get("target_character_key", ""))
            relationship_key = str(edge.get("relationship_key", ""))
            pair = tuple(sorted((source, target)))
            canonical = canonical_by_pair.get(pair) if source and target else None
            if canonical is None:
                deduplicated_edges.append(edge)
                if source and target:
                    canonical_by_pair[pair] = edge
                if relationship_key:
                    relationship_key_aliases[relationship_key] = relationship_key
                continue

            canonical_key = str(canonical.get("relationship_key", ""))
            if relationship_key and canonical_key:
                relationship_key_aliases[relationship_key] = canonical_key
            canonical_types = [str(item) for item in canonical.get("relationship_types", [])]
            duplicate_types = [str(item) for item in edge.get("relationship_types", [])]
            canonical["relationship_types"] = list(
                dict.fromkeys([*canonical_types, *duplicate_types])
            )
            canonical["is_core"] = bool(canonical.get("is_core")) or bool(edge.get("is_core"))
            canonical["locked"] = bool(canonical.get("locked")) or bool(edge.get("locked"))

        graph["edges"] = deduplicated_edges
        for ordinal, edge in enumerate(deduplicated_edges, start=1):
            edge["ordinal"] = ordinal
        graph["core_relationship_keys"] = [
            str(edge.get("relationship_key"))
            for edge in deduplicated_edges
            if edge.get("is_core") is True and edge.get("relationship_key")
        ]
    else:
        relationship_key_aliases = {}

    beats = graph.get("beats")
    if not isinstance(beats, list):
        return normalized
    valid_beats = [beat for beat in beats if isinstance(beat, dict)]
    for ordinal, beat in enumerate(valid_beats, start=1):
        beat["ordinal"] = ordinal
        relationship_key = str(beat.get("relationship_key", ""))
        if relationship_key in relationship_key_aliases:
            beat["relationship_key"] = relationship_key_aliases[relationship_key]

    def sortable_int(value: object, fallback: int) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return fallback

    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for position, beat in enumerate(valid_beats, start=1):
        key = (
            str(beat.get("relationship_key", "")),
            sortable_int(beat.get("episode_ordinal"), 1),
        )
        beat["_normalization_position"] = position
        grouped.setdefault(key, []).append(beat)

    for group in grouped.values():
        ordered = sorted(
            group,
            key=lambda beat: (
                sortable_int(beat.get("sequence"), 10_000),
                sortable_int(beat.get("scene_ordinal"), 10_000),
                sortable_int(beat.get("ordinal"), 10_000),
                sortable_int(beat.get("_normalization_position"), 10_000),
            ),
        )
        for sequence, beat in enumerate(ordered, start=1):
            beat["sequence"] = sequence
            if sequence > 1:
                previous_after = ordered[sequence - 2].get("after_state")
                if isinstance(previous_after, dict):
                    beat["before_state"] = json.loads(
                        json.dumps(previous_after, ensure_ascii=False)
                    )

    for beat in valid_beats:
        beat.pop("_normalization_position", None)
    return normalized


RELATIONSHIP_REPAIR_REQUIREMENTS = {
    "INVALID_CHARACTER_REFERENCE": "只引用 Story Bible 中真实存在的 character key。",
    "CORE_CHARACTER_ISOLATED": "为该核心角色补充至少一条有效关系。",
    "MISSING_CORE_RELATIONSHIP": (
        "至少指定一条 is_core=true 的核心关系，并同步 core_relationship_keys。"
    ),
    "MISSING_PRIMARY_CONFLICT": "至少保留一条具有明确冲突强度或 RIVAL/CONTROL 类型的关系。",
    "MISSING_RELATIONSHIP_BEAT": "至少生成一个包含事件、证据和前后状态变化的 Relationship Beat。",
    "HIDDEN_RELATIONSHIP_WITHOUT_REVEAL": (
        "为该 relationship_key 增加 trigger_type 为 REVEAL 或 AUTHENTICATION 的 Beat；"
        "Beat 必须使用完全相同的 relationship_key，不得通过抹平明面关系与真实关系来规避。"
    ),
}


def _relationship_repair_requirement(issue: RelationshipGraphValidationIssue) -> str:
    return RELATIONSHIP_REPAIR_REQUIREMENTS.get(issue.code, issue.message)


def _validate_story_structure_relationships(value: BaseModel) -> None:
    if not isinstance(value, StoryStructure):
        raise TypeError("角色关系语义校验只接受 StoryStructure")
    issues = validate_relationship_graph(
        value.relationship_graph,
        value.story_bible.model_dump(mode="json"),
    )
    blockers = [item for item in issues if item.severity == "BLOCKER"]
    if not blockers:
        return

    hidden_issues = [item for item in blockers if item.code == "HIDDEN_RELATIONSHIP_WITHOUT_REVEAL"]
    blocking_relationship_keys = sorted(
        {item.relationship_key for item in blockers if item.relationship_key}
    )
    hidden_relationship_keys = sorted(
        {item.relationship_key for item in hidden_issues if item.relationship_key}
    )
    summary_parts: list[str] = []
    if hidden_issues:
        summary_parts.append(f"{len(hidden_issues)} 条隐藏关系缺少揭示计划")
    other_blocker_count = len(blockers) - len(hidden_issues)
    if other_blocker_count:
        summary_parts.append(f"另有 {other_blocker_count} 个关系语义阻断项")
    summary = "；".join(summary_parts) or f"存在 {len(blockers)} 个关系语义阻断项"

    repair_lines = []
    for issue in blockers:
        target = (
            f"relationship_key={issue.relationship_key}"
            if issue.relationship_key
            else f"character_key={issue.character_key}"
            if issue.character_key
            else "关系网整体"
        )
        repair_lines.append(
            f"- {target} | {issue.code} | 缺失要求：{_relationship_repair_requirement(issue)}"
        )
    raise ModelOutputSemanticError(
        "RELATIONSHIP_GRAPH_SEMANTIC_INVALID",
        f"角色关系语义校验失败：{summary}",
        repair_message=(
            "角色关系语义校验未通过。逐项修复以下阻断项；所有隐藏关系都必须在 beats 中"
            "存在 relationship_key 完全相同、trigger_type 为 REVEAL 或 AUTHENTICATION 的 Beat。\n"
            + "\n".join(repair_lines)
        ),
        details={
            "issues": [item.model_dump(mode="json") for item in blockers],
            "blocking_relationship_keys": blocking_relationship_keys,
            "hidden_relationship_keys": hidden_relationship_keys,
            "hidden_relationship_count": len(hidden_issues),
        },
    )


class ScriptPackageOutput(BaseModel):
    outlines: list[EpisodeOutline] = Field(min_length=1, max_length=12)
    scripts: list[EpisodeScript] = Field(min_length=1, max_length=12)
    critic: dict[str, Any]

    @model_validator(mode="after")
    def validate_episode_coverage(self) -> "ScriptPackageOutput":
        outline_ids = [item.episode_ordinal for item in self.outlines]
        script_ids = [item.episode_ordinal for item in self.scripts]
        if len(outline_ids) != len(set(outline_ids)) or len(script_ids) != len(set(script_ids)):
            raise ValueError("分集编号不能重复")
        if script_ids[0] != 1 or 1 not in outline_ids:
            raise ValueError("首集 Outline 与 Script 必须存在")
        return self


class ScriptPackageOutlines(BaseModel):
    outlines: list[EpisodeOutline] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_first_episode(self) -> "ScriptPackageOutlines":
        if 1 not in [item.episode_ordinal for item in self.outlines]:
            raise ValueError("首集 Outline 必须存在")
        return self


class EpisodeScriptDraft(BaseModel):
    episode_ordinal: Literal[1] = 1
    title: str
    canonical_language: str
    scenes: list[ScriptScenePayload] = Field(min_length=2, max_length=8)


class NarrativeReview(BaseModel):
    short_drama_engine: ShortDramaEngine
    breakout_engine: BreakoutNarrativeEngine
    critic: dict[str, Any]


class ScriptExcerptRewriteOutput(BaseModel):
    rewritten_text: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True)
class TextGenerationResult:
    payload: dict[str, Any]
    provider: str
    model: str
    request_id: str | None
    repair_attempts: int


def _scene_ordinal_for_time(scenes: list[ScriptScenePayload], at_ms: int) -> int:
    elapsed = 0
    for ordinal, scene in enumerate(scenes, start=1):
        elapsed += scene.duration_ms
        if at_ms < elapsed:
            return ordinal
    return len(scenes)


def assemble_story_package(
    foundation_result: TextGenerationResult,
    script_result: TextGenerationResult,
    review_result: TextGenerationResult,
) -> TextGenerationResult:
    """Compose split model outputs and deterministically repair mechanical invariants."""
    foundation = StoryFoundation.model_validate(foundation_result.payload)
    draft = EpisodeScriptDraft.model_validate(script_result.payload)
    review = NarrativeReview.model_validate(review_result.payload)
    script = _assemble_episode_script(draft, review)
    package = StoryPackage.model_validate(
        {
            **foundation.model_dump(mode="json"),
            "scripts": [script.model_dump(mode="json")],
            "critic": review.critic,
        }
    )
    return TextGenerationResult(
        payload=package.model_dump(mode="json"),
        provider=review_result.provider,
        model=review_result.model,
        request_id=review_result.request_id,
        repair_attempts=(
            foundation_result.repair_attempts
            + script_result.repair_attempts
            + review_result.repair_attempts
        ),
    )


def assemble_script_package(
    outlines_result: TextGenerationResult,
    script_result: TextGenerationResult,
    review_result: TextGenerationResult,
) -> TextGenerationResult:
    """分阶段合并大纲/剧本草稿/叙事引擎，并修补可机械修复的合同字段。"""
    outlines = ScriptPackageOutlines.model_validate(outlines_result.payload)
    draft = EpisodeScriptDraft.model_validate(script_result.payload)
    review = NarrativeReview.model_validate(review_result.payload)
    script = _assemble_episode_script(draft, review)
    package = ScriptPackageOutput.model_validate(
        {
            "outlines": [item.model_dump(mode="json") for item in outlines.outlines],
            "scripts": [script.model_dump(mode="json")],
            "critic": review.critic,
        }
    )
    return TextGenerationResult(
        payload=package.model_dump(mode="json"),
        provider=review_result.provider,
        model=review_result.model,
        request_id=review_result.request_id,
        repair_attempts=(
            outlines_result.repair_attempts
            + script_result.repair_attempts
            + review_result.repair_attempts
        ),
    )


def _assemble_episode_script(
    draft: EpisodeScriptDraft,
    review: NarrativeReview,
) -> EpisodeScript:
    duration_ms = sum(scene.duration_ms for scene in draft.scenes)

    short_engine = review.short_drama_engine.model_copy(deep=True)
    short_engine = _ensure_short_drama_engine_contract(short_engine)
    beat_count = len(short_engine.beats)
    for index, beat in enumerate(short_engine.beats, start=1):
        beat.sequence = index
        beat.at_ms = min(duration_ms - 1, round((index - 1) * duration_ms / beat_count))
        beat.scene_ordinal = _scene_ordinal_for_time(draft.scenes, beat.at_ms)

    breakout = review.breakout_engine.model_copy(deep=True)
    for items in (breakout.misjudgment_chain, breakout.authentication_ladder):
        previous_scene = 1
        for index, item in enumerate(items, start=1):
            item.sequence = index
            item.scene_ordinal = max(previous_scene, min(len(draft.scenes), item.scene_ordinal))
            previous_scene = item.scene_ordinal
    valid_auth_sequences = {item.sequence for item in breakout.authentication_ladder}
    fallback_auth_sequence = max(valid_auth_sequences)
    for reorder in breakout.relationship_reorders:
        if reorder.trigger_auth_sequence not in valid_auth_sequences:
            reorder.trigger_auth_sequence = fallback_auth_sequence
    breakout.sequel_unit.current_unit_closure = short_engine.stage_closure
    breakout.sequel_unit.next_unit_trigger = short_engine.continuation_hook

    return EpisodeScript.model_validate(
        {
            **draft.model_dump(mode="json"),
            "estimated_duration_ms": duration_ms,
            "short_drama_engine": short_engine.model_dump(mode="json"),
            "breakout_engine": breakout.model_dump(mode="json"),
        }
    )


def _ensure_short_drama_engine_contract(engine: ShortDramaEngine) -> ShortDramaEngine:
    """补齐模型常漏的 CLOSURE，避免整包因单一节拍类型失败。"""
    payload = engine.model_dump(mode="json")
    beats = list(payload.get("beats") or [])
    beat_types = [str(item.get("beat_type") or "") for item in beats]
    if "CLOSURE" not in beat_types and beats:
        insert_at = len(beats) - 1 if beat_types[-1] == "CONTINUATION_HOOK" else len(beats)
        reference = beats[max(0, insert_at - 1)]
        beats.insert(
            insert_at,
            {
                "sequence": insert_at + 1,
                "scene_ordinal": reference.get("scene_ordinal", 1),
                "beat_type": "CLOSURE",
                "at_ms": reference.get("at_ms", 0),
                "description": str(payload.get("stage_closure") or "完成本阶段关系闭环"),
                "story_state_change": str(payload.get("stage_closure") or "阶段关系暂时落定"),
            },
        )
        for index, beat in enumerate(beats, start=1):
            beat["sequence"] = index
        payload["beats"] = beats
    return ShortDramaEngine.model_validate(payload)


def normalize_episode_script_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """去掉空台词场景，并钳制台词时长字段，避免方舟越界直接撞合同。"""
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    scenes: list[dict[str, Any]] = []
    for scene in normalized.get("scenes", []):
        if not isinstance(scene, dict):
            continue
        lines = scene.get("lines")
        if not isinstance(lines, list) or not lines:
            continue
        cleaned_lines: list[dict[str, Any]] = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            pause_after_ms = line.get("pause_after_ms")
            if isinstance(pause_after_ms, (int, float)):
                line["pause_after_ms"] = max(0, min(3000, int(pause_after_ms)))
            estimated_duration_ms = line.get("estimated_duration_ms")
            if isinstance(estimated_duration_ms, (int, float)):
                value = int(estimated_duration_ms)
                line["estimated_duration_ms"] = 800 if value < 200 else min(20_000, value)
            elif not estimated_duration_ms:
                line["estimated_duration_ms"] = 800
            speech_rate = line.get("speech_rate")
            if isinstance(speech_rate, (int, float)):
                line["speech_rate"] = max(0.7, min(1.4, float(speech_rate)))
            cleaned_lines.append(line)
        if not cleaned_lines:
            continue
        scene["lines"] = cleaned_lines
        scenes.append(scene)
    if len(scenes) >= 2:
        normalized["scenes"] = scenes
    return normalized


class TextProvider(Protocol):
    async def generate_directions(
        self,
        settings: Settings,
        brief: dict[str, Any],
        *,
        existing_results: dict[str, TextGenerationResult] | None = None,
        on_route_complete: Callable[[str, TextGenerationResult], Awaitable[None]] | None = None,
    ) -> TextGenerationResult: ...

    async def generate_story_package(
        self, settings: Settings, brief: dict[str, Any], direction: dict[str, Any]
    ) -> TextGenerationResult: ...

    async def generate_story_structure(
        self,
        settings: Settings,
        brief: dict[str, Any],
        direction: dict[str, Any],
        *,
        on_model_output: Callable[[int], Awaitable[None]] | None = None,
        on_validation_failure: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> TextGenerationResult: ...

    async def generate_script_package(
        self,
        settings: Settings,
        brief: dict[str, Any],
        direction: dict[str, Any],
        story_bible: dict[str, Any],
        relationship_graph: dict[str, Any],
    ) -> TextGenerationResult: ...


def _split_durations(total: int) -> list[int]:
    first = max(12, round(total * 0.3))
    second = max(18, round(total * 0.4))
    return [first, second, total - first - second]


def _mock_direction(
    brief: dict[str, Any], key: str, label: str, differentiator: str, ending: str
) -> dict[str, Any]:
    narrative_targeting = targeting_from_brief(brief)
    reward_labels = [
        EMOTIONAL_REWARD_LABELS.get(str(item), str(item))
        for item in narrative_targeting["emotional_rewards"]
    ]
    audience_label = TARGET_AUDIENCE_LABELS.get(
        str(narrative_targeting["target_audience"]),
        str(narrative_targeting["target_audience"]),
    )
    total = int(brief.get("target_duration_sec", 60))
    durations = _split_durations(total)
    shots_by_scene = (["S01", "S02"], ["S03", "S04", "S05"], ["S06", "S07", "S08"])
    scenes = []
    shot_sizes = ("WS", "CU", "MCU", "MS", "WS", "MS", "CU", "WS")
    cameras = ("TRACK", "STATIC", "DOLLY_IN", "STATIC", "DOLLY_IN", "PAN", "STATIC", "TRACK")
    shot_cursor = 0
    for scene_index, (scene_duration, shot_codes) in enumerate(
        zip(durations, shots_by_scene, strict=True), start=1
    ):
        base = scene_duration // len(shot_codes)
        remainder = scene_duration % len(shot_codes)
        shots = []
        for index, code in enumerate(shot_codes):
            shots.append(
                {
                    "code": code,
                    "duration_sec": base + (1 if index < remainder else 0),
                    "shot_size": shot_sizes[shot_cursor],
                    "camera": cameras[shot_cursor],
                }
            )
            shot_cursor += 1
        scenes.append(
            {
                "code": f"{scene_index:02d}",
                "title": ("危机落地", "秘密交换", "代价显现")[scene_index - 1],
                "purpose": (
                    "前三秒明确人物困境并建立观看承诺",
                    f"通过{label}路线让人物主动选择并暴露秘密",
                    "完成可见反转，并留下可延展悬念",
                )[scene_index - 1],
                "duration_sec": scene_duration,
                "shots": shots,
            }
        )
    raw_input = str(brief["raw_input"])
    direction_details = {
        "emotion": {
            "audience_fit": "偏爱情感关系、人物抉择与余韵反转的观众",
            "visual_signature": "克制近景与关系距离变化，让信任崩塌发生在人物表情里",
            "selection_tradeoff": "情绪共鸣最强，但需要演员表演和前置信任铺垫支撑反转",
            "stakes": "主角若判断错误，将亲手伤害唯一仍愿意相信自己的人",
            "payoff": "观众先经历背叛刺痛，再看到主角以行动重新夺回关系主动权",
            "key_turns": [
                "最亲近的人被指认为嫌疑人",
                "主角为保护对方主动隐瞒关键证据",
                "被保护者公开第二个秘密并改写双方关系",
            ],
            "risk_notes": ["情感误判必须由可见线索支撑，避免只靠对白解释"],
            "closure": "主角查清眼前背叛的真实原因，并为自己的误判付出关系代价",
            "reveal": "被信任的人交出一份只可能来自幕后操盘者的秘密名单",
            "next_conflict": "名单首位是主角以为早已死亡的家人，而对方正在清除所有知情者",
            "next_objective": "主角必须在下一部抢先找到家人并确认其究竟是受害者还是共谋者",
        },
        "plot": {
            "audience_fit": "偏爱高密度线索、限时任务与连续反转的观众",
            "visual_signature": "倒计时、可验证物证与空间调度共同推动每次局势翻盘",
            "selection_tradeoff": "追看驱动力最强，但线索因果必须严密，制作调度复杂度更高",
            "stakes": "主角若未在时限内拼出真相，证据会被销毁且无辜者将承担罪名",
            "payoff": "观众跟随主角完成一次可复盘的破局，并在胜利瞬间发现更大棋局",
            "key_turns": [
                "倒计时启动且唯一出口被封锁",
                "主角夺到的证据反而证明自己是嫌疑人",
                "主角利用前置漏洞反制执行者并逼出幕后指令",
            ],
            "risk_notes": ["每次反转必须回收前置物证，不能依赖突然出现的新信息"],
            "closure": "主角在倒计时结束前保住证据并洗清眼前指控",
            "reveal": "被捕的执行者远程启动第二阶段计划，屏幕上出现新的目标坐标",
            "next_conflict": "幕后者将下一轮行动转向主角最想保护的人，并掌握其行动轨迹",
            "next_objective": "主角必须沿坐标追出控制中心，在第二阶段完成前切断幕后者的监控链",
        },
        "market": {
            "audience_fit": "偏爱身份逆袭、即时爽点与强传播话题的短视频观众",
            "visual_signature": "公开场合的身份翻盘、权力位置交换与高辨识度英雄时刻",
            "selection_tradeoff": "即时爽感和传播性最强，但需避免只剩标签化逆袭而缺少人物代价",
            "stakes": "主角若无法守住新身份，不仅会失去翻盘机会，还会牵连真正帮助自己的人",
            "payoff": "主角在众目睽睽下反转权力关系，让此前的压迫者承担即时后果",
            "key_turns": [
                "主角被公开剥夺身份与资源",
                "隐藏凭证使权力关系第一次倒转",
                "主角赢下当局却暴露新身份的致命来源",
            ],
            "risk_notes": ["爽点必须改变资源与权力状态，避免停留在口头反击"],
            "closure": "主角凭新身份赢下当局，并夺回被抢走的关键资源",
            "reveal": "新身份的真正持有人现身，当场冻结主角刚获得的全部权限",
            "next_conflict": "真正持有人要求主角交出资源，否则将公开其身份来源并追责所有盟友",
            "next_objective": (
                "主角必须在下一部找到身份授权的原始证据，并抢在公开审判前建立自己的合法筹码"
            ),
        },
    }[key]
    requirements = [str(item) for item in brief.get("content_requirements", [])]
    avoidances = [str(item) for item in brief.get("content_avoidances", [])]
    compliance_items = [
        {
            "category": "REQUIREMENT",
            "item": item,
            "status": "PARTIAL",
            "evidence": "方向结构已继承该约束，仍需在完整剧本和分镜中逐条核验。",
        }
        for item in requirements
    ] + [
        {
            "category": "AVOIDANCE",
            "item": item,
            "status": "MET",
            "evidence": "当前方向未出现明确冲突，生成完整剧本后继续复核。",
        }
        for item in avoidances
    ]
    shot_count = sum(len(scene["shots"]) for scene in scenes)
    recommendation_matches = requirements[:3] or [
        f"主平台：{brief.get('target_platform', 'douyin')}",
        f"目标受众：{audience_label}",
        f"情绪回报：{'、'.join(reward_labels)}",
        f"目标时长：{total} 秒",
    ]
    return {
        "narrative_targeting": narrative_targeting,
        "direction_key": key,
        "title": f"{brief['project_name']} · {label}",
        "logline": raw_input,
        "director_statement": f"以{brief['style']}为基调，从{differentiator}切入冲突。",
        "differentiator": differentiator,
        "audience_fit": f"{audience_label}；{direction_details['audience_fit']}",
        "visual_signature": direction_details["visual_signature"],
        "selection_tradeoff": direction_details["selection_tradeoff"],
        "key_turns": direction_details["key_turns"],
        "risk_notes": direction_details["risk_notes"],
        "sequel_setup": {
            "current_arc_closure": direction_details["closure"],
            "final_reveal_or_action": direction_details["reveal"],
            "next_installment_conflict": direction_details["next_conflict"],
            "next_installment_objective": direction_details["next_objective"],
        },
        "total_duration_sec": total,
        "scenes": scenes,
        "assumptions": [
            f"主市场以 {brief.get('primary_market', 'CN')} 为准",
            f"规范语言为 {brief.get('canonical_language', 'zh-CN')}",
            "未确认的制作细节保留为可编辑创意默认值",
        ],
        "story_dna": {
            "core_premise": raw_input,
            "protagonist_want": "立刻摆脱眼前危机并守住秘密",
            "protagonist_need": "承认真实选择所带来的代价",
            "central_conflict": differentiator,
            "stakes": direction_details["stakes"],
            "emotional_promise": f"以{'、'.join(reward_labels)}为核心的阶段性兑现",
            "payoff": direction_details["payoff"],
            "ending_hook": ending,
            "tone_keywords": ["高压", "克制", "现实", "反转"],
        },
        "brief_compliance": {
            "status": "PARTIAL" if requirements else "ALL_MET",
            "items": compliance_items,
        },
        "production_complexity": {
            "character_count": 4,
            "scene_count": len(scenes),
            "exterior_scene_count": 1,
            "exterior_requirements": ["一处可控夜外景，需保持天气与光线连续"],
            "vfx_requirements": ["环境氛围增强", "关键物证或界面特写"],
            "estimated_generation": {
                "keyframe_images": shot_count,
                "video_clips": shot_count,
                "voice_segments": max(6, len(scenes) * 2),
            },
        },
        "first_episode_rhythm": {
            "opening_3s_hook": "前 3 秒直接呈现主角被公开剥夺身份或资源的危机。",
            "first_payoff": direction_details["payoff"],
            "ending_action": direction_details["reveal"],
        },
        "ai_recommendation": {
            "recommended": key == "market",
            "brief_matches": recommendation_matches,
            "reason": (
                "该方向把平台需要的即时钩子、核心观众的情绪期待和"
                "可续作动作放在同一条因果链上，不需依赖虚构评分。"
            ),
        },
    }


def deterministic_directions(brief: dict[str, Any]) -> StoryDirectionBatch:
    batch = StoryDirectionBatch.model_validate(
        {
            "directions": [
                _mock_direction(
                    brief,
                    "emotion",
                    "情绪悬疑",
                    "关系背叛与情感误判",
                    "被信任的人交出幕后秘密名单，名单首位正是主角以为早已死亡的家人",
                ),
                _mock_direction(
                    brief,
                    "plot",
                    "强情节反转",
                    "线索争夺与时间压力",
                    "被捕的执行者启动第二阶段计划，并把新目标锁定为主角最想保护的人",
                ),
                _mock_direction(
                    brief,
                    "market",
                    "市场钩子",
                    "身份错位与即时爽点",
                    "新身份的真正持有人现身，并冻结主角刚赢得的全部权限",
                ),
            ]
        }
    )
    for direction in batch.directions:
        _validate_targeting_contract(direction.model_dump(mode="json"), brief)
    return batch


def _replace_deterministic_text(value: Any, replacements: tuple[tuple[str, str], ...]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_deterministic_text(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_deterministic_text(item, replacements) for item in value]
    if isinstance(value, str):
        for source, replacement in replacements:
            value = value.replace(source, replacement)
    return value


def _adapt_deterministic_protagonist(
    package: dict[str, Any], brief: dict[str, Any]
) -> dict[str, Any]:
    protagonist = str(brief["narrative_protagonist"])
    profiles = {
        "male": ("沈屿", "他", "male", "当代都市男性，雨夜通勤装，克制但警觉"),
        "female": ("林岚", "她", "female", "当代都市女性，雨夜通勤装，克制但警觉"),
        "dual": ("林言", "林言", "unspecified", "双主角之一，雨夜通勤装，克制但警觉"),
        "ensemble": (
            "受困众人",
            "众人",
            "unspecified",
            "由不同年龄与性别人物构成的受困群像，造型各异但空间连续",
        ),
    }
    if protagonist not in profiles:
        raise ValueError("生成故事前必须明确叙事主角")
    name, pronoun, gender, visual_notes = profiles[protagonist]
    adapted = _replace_deterministic_text(
        package,
        (
            ("当代都市女性，雨夜通勤装，克制但警觉", visual_notes),
            ("林岚", name),
            ("她", pronoun),
        ),
    )
    characters = adapted["story_bible"]["characters"]
    characters[0]["gender"] = gender
    if protagonist == "dual":
        characters[1]["role"] = "PROTAGONIST"
    return adapted


def deterministic_story_package(brief: dict[str, Any], direction: dict[str, Any]) -> StoryPackage:
    narrative_targeting = targeting_from_brief(brief)
    reward_labels = [
        EMOTIONAL_REWARD_LABELS.get(str(item), str(item))
        for item in narrative_targeting["emotional_rewards"]
    ]
    reward_phrase = "、".join(reward_labels)
    total_ms = int(brief.get("target_duration_sec", 60)) * 1000
    scene_ms = [round(total_ms * 0.3), round(total_ms * 0.4)]
    scene_ms.append(total_ms - sum(scene_ms))
    localization_targets = brief.get("localization_targets", [])
    scenes = []
    line_sets = (
        [
            ("NARRATOR", "灯灭的那一刻，所有人的手机同时收到一张旧照片。", "VOICE_OVER"),
            ("protagonist", "谁发的？", "DIALOGUE"),
        ],
        [
            ("witness", "不是谁发的，是谁一直留着它。", "DIALOGUE"),
            ("protagonist", "把门锁上。今天谁都别走。", "DIALOGUE"),
        ],
        [
            ("NARRATOR", "应急灯亮起，照片里缺失的人正站在门外。", "VOICE_OVER"),
            ("protagonist", "原来你才是最后一个。", "DIALOGUE"),
        ],
    )
    for scene_index, (duration, lines) in enumerate(zip(scene_ms, line_sets, strict=True), start=1):
        line_duration = max(1200, (duration - 1000) // len(lines))
        scenes.append(
            {
                "heading": f"场景 {scene_index} · 便利店",
                "location": "城市便利店",
                "time_of_day": "夜",
                "purpose": direction["scenes"][scene_index - 1]["purpose"],
                "emotion": ("警觉", "对峙", "震惊")[scene_index - 1],
                "duration_ms": duration,
                "bgm_intent": ("低频脉冲", "弦乐压力渐强", "骤停后单音悬念")[scene_index - 1],
                "sfx_intents": (["雷声", "断电"], ["门锁", "雨声"], ["应急灯", "敲门"])[
                    scene_index - 1
                ],
                "lines": [
                    {
                        "speaker_key": speaker,
                        "text": text,
                        "line_type": line_type,
                        "emotion": ("紧张", "克制", "震惊")[scene_index - 1],
                        "speech_rate": 1.0,
                        "pause_after_ms": 400,
                        "estimated_duration_ms": line_duration,
                        "pronunciation": {},
                        "localizations": {target: text for target in localization_targets},
                    }
                    for speaker, text, line_type in lines
                ],
            }
        )
    beat_at = lambda ratio: min(total_ms - 1, round(total_ms * ratio))  # noqa: E731
    package = {
        "story_bible": {
            "narrative_targeting": narrative_targeting,
            "world": "当代城市暴雨夜，便利店成为封闭且可验证线索的临时孤岛。",
            "rules": ["秘密必须通过可见行动揭示", "每次反转都回收一个既有线索"],
            "characters": [
                {
                    "key": "protagonist",
                    "name": "林岚",
                    "role": "PROTAGONIST",
                    "gender": "female",
                    "age": "28岁",
                    "occupation": "都市职场从业者",
                    "personality": ["克制", "警觉", "追根究底"],
                    "dramatic_function": "主动封锁现场并追问真相的人",
                    "desire": "找出照片来源",
                    "fear": "自己的旧身份曝光",
                    "secret": "她认识照片中缺失的人",
                    "visual_notes": "当代都市女性，雨夜通勤装，克制但警觉",
                },
                {
                    "key": "witness",
                    "name": "周启",
                    "role": "SUPPORTING",
                    "gender": "male",
                    "age": "32岁",
                    "occupation": "自由摄影师",
                    "personality": ["冷静", "执着", "防备心强"],
                    "dramatic_function": "掌握局部真相但动机不明的见证者",
                    "desire": "迫使主角承认过去",
                    "fear": "真相再次被掩盖",
                    "secret": "他保存了照片原件",
                    "visual_notes": "湿透的深色外套，表情冷静，始终靠近出口",
                },
            ],
            "relationships": ["林岚与周启共享一段被删除的过去"],
            "foreshadowing": ["照片裁切边缘", "门外反复出现的影子"],
            "continuity_rules": [
                "暴雨始终持续",
                "应急灯在第三场前不得亮起",
                "照片不能离开主角视线",
            ],
        },
        "outlines": [
            {
                "episode_ordinal": 1,
                "title": str(direction["title"]),
                "hook": "断电与群发旧照片同时发生",
                "objective": "确定照片发送者与被裁掉的人",
                "conflict": str(direction["story_dna"]["central_conflict"]),
                "turn": "保留照片的人主动暴露",
                "cliffhanger": str(direction["story_dna"]["ending_hook"]),
                "target_duration_sec": int(brief.get("target_duration_sec", 60)),
            }
        ],
        "scripts": [
            {
                "episode_ordinal": 1,
                "title": str(direction["title"]),
                "canonical_language": str(brief.get("canonical_language", "zh-CN")),
                "estimated_duration_ms": total_ms,
                "short_drama_engine": {
                    "formula_version": SHORT_DRAMA_FORMULA_VERSION,
                    "formula": SHORT_DRAMA_FORMULA,
                    "protagonist_desire": "找出照片来源，守住旧身份，并掌控被封锁的现场",
                    "pace_strategy": "每个场景都引入新信息、迫使人物选择并改变现场权力关系",
                    "payoff_strategy": (
                        f"先兑现锁门控场与逼问见证者的小胜利，再把{reward_phrase}升级为情绪回报"
                    ),
                    "reversal_chain": [
                        "照片不是临时群发，而是见证者一直保存着原件",
                        "照片中被裁掉的人并未消失，而是正站在门外",
                    ],
                    "stage_closure": "主角确认照片原件的持有人并夺回现场主动权",
                    "continuation_hook": str(direction["story_dna"]["ending_hook"]),
                    "beats": [
                        {
                            "sequence": 1,
                            "scene_ordinal": 1,
                            "beat_type": "HOOK",
                            "at_ms": 0,
                            "description": "断电与群发旧照片同时发生",
                            "story_state_change": "普通避雨现场变成所有人都被卷入的秘密危机",
                        },
                        {
                            "sequence": 2,
                            "scene_ordinal": 1,
                            "beat_type": "ESCALATION",
                            "at_ms": beat_at(0.18),
                            "description": "主角公开追问发送者",
                            "story_state_change": "主角从被动接收信息转为主动调查",
                        },
                        {
                            "sequence": 3,
                            "scene_ordinal": 2,
                            "beat_type": "PAYOFF",
                            "at_ms": beat_at(0.36),
                            "description": "主角锁门控场，不再允许任何人离开",
                            "story_state_change": "主角获得阶段性控制权并兑现反击爽点",
                        },
                        {
                            "sequence": 4,
                            "scene_ordinal": 2,
                            "beat_type": "REVERSAL",
                            "at_ms": beat_at(0.48),
                            "description": "见证者承认自己一直保存照片原件",
                            "story_state_change": "嫌疑从未知发送者转向掌握旧案证据的熟人",
                        },
                        {
                            "sequence": 5,
                            "scene_ordinal": 2,
                            "beat_type": "ESCALATION",
                            "at_ms": beat_at(0.64),
                            "description": "见证者迫使主角面对被删除的过去",
                            "story_state_change": "调查目标升级为主角身份是否会曝光",
                        },
                        {
                            "sequence": 6,
                            "scene_ordinal": 3,
                            "beat_type": "REVERSAL",
                            "at_ms": beat_at(0.8),
                            "description": "照片中缺失的人出现在门外",
                            "story_state_change": "旧案证据变成正在逼近的现实威胁",
                        },
                        {
                            "sequence": 7,
                            "scene_ordinal": 3,
                            "beat_type": "CLOSURE",
                            "at_ms": beat_at(0.9),
                            "description": "主角确认照片原件的持有人和第一层真相",
                            "story_state_change": "本集关于谁掌握照片的疑问完成阶段闭环",
                        },
                        {
                            "sequence": 8,
                            "scene_ordinal": 3,
                            "beat_type": "CONTINUATION_HOOK",
                            "at_ms": beat_at(0.97),
                            "description": str(direction["story_dna"]["ending_hook"]),
                            "story_state_change": "已关闭的小疑问打开更大的身份与幕后危机",
                        },
                    ],
                },
                "breakout_engine": {
                    "formula_version": BREAKOUT_FORMULA_VERSION,
                    "formula": BREAKOUT_FORMULA,
                    "vulnerable_shell": (
                        "林岚表面上只是暴雨夜被困在便利店、旧身份随时可能曝光的普通通勤者"
                    ),
                    "elite_core": (
                        "她拥有在高压现场迅速识别证据矛盾、控制局面并判断证词真假的顶级能力"
                    ),
                    "misjudgment_chain": [
                        {
                            "sequence": 1,
                            "scene_ordinal": 1,
                            "observer_key": "witness",
                            "mistaken_belief": "周启认定林岚只想逃避旧案并继续隐藏过去",
                            "resulting_action": "他用旧照片逼迫林岚当众失态",
                            "cost_to_protagonist": "林岚被推到所有人的怀疑中心",
                            "correction_seed": "林岚第一眼就注意到照片裁切边缘与群发时间完全一致",
                        },
                        {
                            "sequence": 2,
                            "scene_ordinal": 2,
                            "observer_key": "trapped_group",
                            "mistaken_belief": "被困者把林岚的锁门举动误判为心虚和控制证据",
                            "resulting_action": "众人拒绝配合并要求她交出手机",
                            "cost_to_protagonist": (
                                "林岚失去现场信任，却必须独自承担阻止真凶离开的压力"
                            ),
                            "correction_seed": "她准确指出照片原件的持有人并复原发送顺序",
                        },
                    ],
                    "authentication_ladder": [
                        {
                            "sequence": 1,
                            "scene_ordinal": 1,
                            "proof_type": "ABILITY",
                            "proof": "林岚从裁切边缘、发送时间和站位中锁定照片并非临时群发",
                            "reveals": "她不是被动受困者，而是具备专业级现场判断能力的人",
                            "who_updates_belief": ["trapped_group"],
                            "status_shift": "从可疑当事人变为暂时值得听取的调查者",
                            "remaining_misjudgment": "众人仍认为她可能利用能力掩盖自己的旧身份",
                        },
                        {
                            "sequence": 2,
                            "scene_ordinal": 2,
                            "proof_type": "TRUTH",
                            "proof": "周启承认自己保存原件，林岚的证据链被当场验证",
                            "reveals": "林岚锁门是为了保护证据和阻止真正知情者离开",
                            "who_updates_belief": ["witness", "trapped_group"],
                            "status_shift": "从被审视者变为现场决策者",
                            "remaining_misjudgment": "她与照片中缺失者的真实关系仍未被认证",
                        },
                    ],
                    "relationship_reorders": [
                        {
                            "relationship_key": "protagonist-witness",
                            "before": "周启掌握照片并以审问者姿态压制林岚",
                            "trigger_auth_sequence": 2,
                            "after": "林岚掌握证据链，周启被迫从控诉者转为受约束的合作证人",
                            "emotional_consequence": "羞耻与防御被重新排列为有条件的信任和共同面对",
                        }
                    ],
                    "emotional_order_rebuild": {
                        "old_order": "林岚因旧身份被动承受怀疑，周启依靠秘密控制叙事",
                        "rupture": "照片原件持有人和证据链被公开验证，原有控诉秩序失效",
                        "new_order": "林岚选择主动面对过去并取得现场领导权，周启必须接受事实约束",
                        "emotional_payoff": (
                            f"主角不再靠隐藏自保，而是通过能力与选择兑现{reward_phrase}回报"
                        ),
                    },
                    "sequel_unit": {
                        "current_unit_closure": "主角确认照片原件的持有人并夺回现场主动权",
                        "unresolved_engine": (
                            "林岚与照片中缺失者的真实关系及群发照片的幕后目的尚未揭晓"
                        ),
                        "next_unit_trigger": str(direction["story_dna"]["ending_hook"]),
                        "escalation_promise": (
                            "下一单元将把能力认证升级为身份认证，并引入更高层幕后操控者"
                        ),
                    },
                },
                "scenes": scenes,
            }
        ],
        "critic": {
            "status": "PASS_WITH_NOTES",
            "checks": {
                "duration_budget": "PASS",
                "hook_present": "PASS",
                "protagonist_desire": "PASS",
                "pace_density": "PASS",
                "emotional_payoff": "PASS",
                "progressive_reversals": "PASS",
                "stage_closure": "PASS",
                "continuation_hook": "PASS",
                "vulnerable_shell": "PASS",
                "elite_core": "PASS",
                "sustained_misjudgment": "PASS",
                "staged_authentication": "PASS",
                "relationship_reorder": "PASS",
                "emotional_order_rebuild": "PASS",
                "sequel_unit": "PASS",
                "continuity": "PASS",
                "content_requirements": "REVIEW",
            },
            "notes": ["用户批准前仍需人工核对内容要求与市场语境"],
        },
    }
    adapted = _adapt_deterministic_protagonist(package, brief)
    _validate_targeting_contract(adapted, brief, nested_key="story_bible")
    return StoryPackage.model_validate(adapted)


def deterministic_story_structure(
    brief: dict[str, Any], direction: dict[str, Any]
) -> StoryStructure:
    package = deterministic_story_package(brief, direction)
    before_state = {
        "surface_relationship": "互相审视的嫌疑人与证人",
        "true_relationship": "共享旧案秘密的对立知情者",
        "trust_level": -2,
        "emotional_temperature": -1,
        "power_balance": 1,
        "conflict_intensity": 3,
    }
    after_state = {
        "surface_relationship": "受约束的临时合作方",
        "true_relationship": "共同面对旧案的有条件盟友",
        "trust_level": 0,
        "emotional_temperature": 0,
        "power_balance": 0,
        "conflict_intensity": 1,
    }
    relationship_graph = RelationshipGraphPayload.model_validate(
        {
            "schema_version": "relationship-graph-v1",
            "edges": [
                {
                    "relationship_key": "protagonist-witness",
                    "source_character_key": "protagonist",
                    "target_character_key": "witness",
                    "directionality": "BIDIRECTIONAL",
                    "relationship_types": ["RIVAL", "SECRET"],
                    "surface_relationship": before_state["surface_relationship"],
                    "true_relationship": before_state["true_relationship"],
                    "source_view": {
                        "perceived_relationship": "可能隐瞒真相的证人",
                        "belief": "周启掌握照片原件但没有说出全部事实",
                    },
                    "target_view": {
                        "perceived_relationship": "试图逃避过去的嫌疑人",
                        "belief": "林岚仍在控制证据和现场叙事",
                    },
                    "trust_level": -2,
                    "emotional_temperature": -1,
                    "power_balance": 1,
                    "conflict_intensity": 3,
                    "story_function": "通过旧案秘密制造误判，并在认证后完成权力重排",
                    "secret": "两人共同认识照片中被删除的人",
                    "is_core": True,
                    "locked": False,
                    "ordinal": 1,
                }
            ],
            "beats": [
                {
                    "relationship_key": "protagonist-witness",
                    "episode_ordinal": 1,
                    "sequence": 1,
                    "scene_ordinal": 2,
                    "trigger_type": "AUTHENTICATION",
                    "trigger_ref": "authentication:2",
                    "before_state": before_state,
                    "after_state": after_state,
                    "evidence": "周启交出照片原件，验证林岚的证据链",
                    "emotional_consequence": "羞耻与防御转为有限信任",
                    "audience_visibility": "REVEALED",
                    "ordinal": 1,
                }
            ],
            "core_relationship_keys": ["protagonist-witness"],
            "generation_notes": ["结构阶段生成，批准前需要人工核对关系变化。"],
        }
    )
    story_bible = StoryBibleV2.model_validate(
        package.story_bible.model_dump(mode="json", exclude={"relationships"})
    )
    issues = validate_relationship_graph(
        relationship_graph,
        story_bible.model_dump(mode="json"),
    )
    if relationship_graph_has_blockers(issues):
        raise ValueError("确定性关系网不应包含批准阻断项")
    return StoryStructure(
        story_bible=story_bible,
        relationship_graph=relationship_graph,
        critic={
            "status": "PASS_WITH_NOTES",
            "validation_issues": [item.model_dump(mode="json") for item in issues],
            "notes": ["用户批准前仍需人工核对内容要求与市场语境"],
        },
    )


def _relationship_context_indexes(
    relationship_graph: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    edges = {
        str(item["relationship_key"]): item
        for item in relationship_graph.get("edges", [])
        if isinstance(item, dict) and item.get("relationship_key")
    }
    beats = {
        str(item["relationship_beat_id"]): item
        for item in relationship_graph.get("beats", [])
        if isinstance(item, dict) and item.get("relationship_beat_id")
    }
    return edges, beats


def apply_relationship_context_to_script_package(
    package_payload: dict[str, Any], relationship_graph: dict[str, Any]
) -> ScriptPackageOutput:
    payload = json.loads(json.dumps(package_payload, ensure_ascii=False))
    edges, beats = _relationship_context_indexes(relationship_graph)
    for script in payload.get("scripts", []):
        reorders = script.get("breakout_engine", {}).get("relationship_reorders", [])
        for reorder in reorders:
            relationship_key = str(reorder.get("relationship_key", ""))
            edge = edges.get(relationship_key)
            if edge is None:
                continue
            matching_beats = [
                beat for beat in beats.values() if beat.get("relationship_key") == relationship_key
            ]
            if not matching_beats:
                continue
            beat = matching_beats[0]
            reorder.update(
                {
                    "source_character_key": edge["source_character_key"],
                    "target_character_key": edge["target_character_key"],
                    "before_state": beat["before_state"],
                    "after_state": beat["after_state"],
                    "relationship_beat_id": beat["relationship_beat_id"],
                }
            )
    package = ScriptPackageOutput.model_validate(payload)
    validate_script_package_relationship_contract(package, relationship_graph)
    return package


def validate_script_package_relationship_contract(
    package: ScriptPackageOutput,
    relationship_graph: dict[str, Any],
) -> None:
    edges, beats = _relationship_context_indexes(relationship_graph)
    for script in package.scripts:
        valid_auth_sequences = {
            item.sequence for item in script.breakout_engine.authentication_ladder
        }
        for reorder in script.breakout_engine.relationship_reorders:
            edge = edges.get(reorder.relationship_key)
            if edge is None:
                raise ValueError(f"关系重排引用了不存在的关系 {reorder.relationship_key}")
            if reorder.source_character_key != edge.get(
                "source_character_key"
            ) or reorder.target_character_key != edge.get("target_character_key"):
                raise ValueError("关系重排的 source/target 与批准关系网不一致")
            beat = beats.get(str(reorder.relationship_beat_id))
            if beat is None or beat.get("relationship_key") != reorder.relationship_key:
                raise ValueError("关系重排引用了不属于该关系的变化节点")
            if reorder.before_state is None or reorder.after_state is None:
                raise ValueError("关系重排必须提供结构化前后状态")
            if reorder.before_state.model_dump(mode="json") != beat.get("before_state"):
                raise ValueError("关系重排的变化前状态与批准关系网不一致")
            if reorder.after_state.model_dump(mode="json") != beat.get("after_state"):
                raise ValueError("关系重排的变化后状态与批准关系网不一致")
            if reorder.trigger_auth_sequence not in valid_auth_sequences:
                raise ValueError("关系重排引用了不存在的认证步骤")
            if beat.get("trigger_ref") != f"authentication:{reorder.trigger_auth_sequence}":
                raise ValueError("关系变化节点与认证步骤不一致")
            before = reorder.before_state
            after = reorder.after_state
            if (
                before.surface_relationship,
                before.true_relationship,
                before.trust_level,
                before.power_balance,
                before.conflict_intensity,
            ) == (
                after.surface_relationship,
                after.true_relationship,
                after.trust_level,
                after.power_balance,
                after.conflict_intensity,
            ):
                raise ValueError("关系变化不能只改变情绪温度")

        for relationship_key, edge in edges.items():
            reveal_scenes = [
                int(beat["scene_ordinal"])
                for beat in beats.values()
                if beat.get("relationship_key") == relationship_key
                and beat.get("scene_ordinal") is not None
                and (
                    beat.get("audience_visibility") == "REVEALED"
                    or beat.get("trigger_type") in {"REVEAL", "AUTHENTICATION"}
                )
            ]
            if not reveal_scenes:
                continue
            first_reveal_scene = min(reveal_scenes)
            protected_facts = [
                str(value).strip()
                for value in (edge.get("secret"), edge.get("true_relationship"))
                if isinstance(value, str) and len(value.strip()) >= 4
            ]
            if not protected_facts:
                continue
            for scene_ordinal, scene in enumerate(script.scenes, start=1):
                if scene_ordinal >= first_reveal_scene:
                    break
                scene_text = scene.model_dump_json()
                leaked = next((fact for fact in protected_facts if fact in scene_text), None)
                if leaked is not None:
                    raise ValueError(f"关系 {relationship_key} 的秘密在设定揭示场景之前泄露")


def _output_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("output_text"), str):
        return str(payload["output_text"]).strip()
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                return str(content["text"]).strip()
    return None


async def _ark_stream_output(
    response: httpx.Response,
    *,
    request_id: str | None,
    total_timeout_seconds: float,
) -> str:
    lines = response.aiter_lines()
    current_event: str | None = None

    async def next_payload(
        timeout_seconds: float,
        *,
        timeout_code: str,
        timeout_message: str,
    ) -> tuple[str | None, dict[str, Any]] | None:
        nonlocal current_event
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    try:
                        line = await anext(lines)
                    except StopAsyncIteration:
                        return None
                    if line.startswith("event:"):
                        current_event = line.removeprefix("event:").strip() or None
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw = line.removeprefix("data:").strip()
                    if not raw or raw == "[DONE]":
                        return None
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError("方舟流式事件 data 必须是 JSON Object")
                    event_type = payload.get("type")
                    return (
                        str(event_type) if isinstance(event_type, str) else current_event,
                        payload,
                    )
        except TimeoutError as exc:
            raise TextProviderError(
                timeout_code,
                timeout_message,
                retryable=True,
                details={
                    "request_id": request_id,
                    "timeout_seconds": timeout_seconds,
                },
            ) from exc

    first_byte_timeout = min(total_timeout_seconds, 60)
    idle_timeout = min(total_timeout_seconds, 30)
    item = await next_payload(
        first_byte_timeout,
        timeout_code="ARK_TEXT_FIRST_BYTE_TIMEOUT",
        timeout_message=f"火山方舟在 {first_byte_timeout:g} 秒内未返回首个流式事件",
    )
    deltas: list[str] = []
    completed_text: str | None = None
    while item is not None:
        event_type, payload = item
        if event_type == "response.output_text.delta" and isinstance(payload.get("delta"), str):
            deltas.append(str(payload["delta"]))
        elif event_type == "response.output_text.done" and isinstance(payload.get("text"), str):
            completed_text = str(payload["text"])
        elif event_type == "response.completed":
            # 优先保留 output_text.done 的正文；仅在缺失时才从 completed 事件兜底提取，
            # 避免开启深度思考后 completed 载荷中的思考内容覆盖正文
            completed_text = completed_text or _output_text(payload.get("response"))
        elif event_type in {"response.failed", "response.incomplete", "error"}:
            provider_error = payload.get("error")
            if not isinstance(provider_error, dict):
                response_payload = payload.get("response")
                provider_error = (
                    response_payload.get("error")
                    if isinstance(response_payload, dict)
                    and isinstance(response_payload.get("error"), dict)
                    else {}
                )
            message = str(provider_error.get("message") or "火山方舟流式生成未完成")
            raise TextProviderError(
                "ARK_TEXT_STREAM_FAILED",
                message,
                retryable=True,
                details={"request_id": request_id, "event_type": event_type},
            )
        item = await next_payload(
            idle_timeout,
            timeout_code="ARK_TEXT_STREAM_IDLE_TIMEOUT",
            timeout_message=f"火山方舟流式输出连续 {idle_timeout:g} 秒没有新事件",
        )

    output = (completed_text or "".join(deltas)).strip()
    if not output:
        raise ValueError("方舟流式响应缺少 output_text")
    return output


async def _ark_json(
    settings: Settings,
    *,
    prompt: str,
    validator: type[BaseModel],
    transport: httpx.AsyncBaseTransport | None = None,
    payload_normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    semantic_validator: Callable[[BaseModel], None] | None = None,
    on_validated_output: Callable[[int], Awaitable[None]] | None = None,
    on_validation_failure: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None,
    thinking_type: Literal["enabled", "disabled"] = "disabled",
) -> TextGenerationResult:
    if not settings.ark_api_key:
        raise TextProviderError("ARK_API_KEY_MISSING", "服务端未配置 ARK_API_KEY", retryable=False)
    validation_message = ""
    request_id: str | None = None
    attempt_diagnostics: list[dict[str, Any]] = []
    last_semantic_error: ModelOutputSemanticError | None = None
    last_failure_kind = ""
    repair_source_json = ""
    provider_error_text = ""
    for attempt in range(3):
        output: str | None = None
        repair = (
            "\n上一次输出未通过校验。请只返回修复后的完整 JSON，不要解释、补丁或局部片段。"
            "必须以上一轮 JSON 为唯一基线，只修改错误点名的字段；禁止重新命名 key、"
            "替换角色、重建关系边或整体改写。\n"
            "错误与缺失要求：\n"
            + validation_message[:6000]
            + "\n上一轮完整 JSON：\n"
            + repair_source_json[:40_000]
            if validation_message
            else ""
        )
        try:
            total_timeout_seconds = settings.ark_request_timeout_seconds
            socket_timeout = min(total_timeout_seconds, 10)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=socket_timeout,
                    read=None,
                    write=socket_timeout,
                    pool=socket_timeout,
                ),
                transport=transport,
            ) as client:
                try:
                    async with asyncio.timeout(total_timeout_seconds):
                        async with client.stream(
                            "POST",
                            settings.ark_responses_url,
                            headers={
                                "Authorization": f"Bearer {settings.ark_api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": settings.ark_prompt_model,
                                "input": prompt + repair,
                                "thinking": {"type": thinking_type},
                                "max_output_tokens": ARK_TEXT_MAX_OUTPUT_TOKENS,
                                "stream": True,
                            },
                        ) as response:
                            request_id = response.headers.get("x-request-id")
                            if response.status_code >= 400:
                                # 提前读出错误响应体，透出方舟真实错误码（如账号欠费）
                                provider_error_text = (await response.aread()).decode(
                                    "utf-8", "replace"
                                )
                            response.raise_for_status()
                            output = await _ark_stream_output(
                                response,
                                request_id=request_id,
                                total_timeout_seconds=total_timeout_seconds,
                            )
                except TimeoutError as exc:
                    raise TextProviderError(
                        "ARK_TEXT_TOTAL_TIMEOUT",
                        f"火山方舟文本生成超过 {total_timeout_seconds:g} 秒总时限",
                        retryable=True,
                        details={
                            "request_id": request_id,
                            "timeout_seconds": total_timeout_seconds,
                        },
                    ) from exc
                decoded = json.loads(output.removeprefix("```json").removesuffix("```").strip())
                if not isinstance(decoded, dict):
                    raise ValueError("方舟文本响应必须是 JSON Object")
                if payload_normalizer is not None:
                    decoded = payload_normalizer(decoded)
                repair_source_json = json.dumps(decoded, ensure_ascii=False)
                validated = validator.model_validate(decoded)
                repair_source_json = validated.model_dump_json()
                if on_validated_output is not None:
                    await on_validated_output(attempt + 1)
                if semantic_validator is not None:
                    semantic_validator(validated)
                return TextGenerationResult(
                    payload=validated.model_dump(mode="json"),
                    provider="volcengine-ark",
                    model=settings.ark_prompt_model,
                    request_id=request_id,
                    repair_attempts=attempt,
                )
        except ValidationError as exc:
            validation_message = str(exc)
            last_failure_kind = "schema"
            diagnostic = {
                "attempt": attempt + 1,
                "request_id": request_id,
                "error_type": "validation_error",
                "validation_error": validation_message[:4000],
                "output_chars": len(output) if "output" in locals() and output else 0,
            }
            attempt_diagnostics.append(diagnostic)
            if on_validation_failure is not None:
                await on_validation_failure(attempt + 1, diagnostic)
        except ModelOutputSemanticError as exc:
            validation_message = exc.repair_message
            last_semantic_error = exc
            last_failure_kind = "semantic"
            diagnostic = {
                "attempt": attempt + 1,
                "request_id": request_id,
                "error_type": "semantic_validation_error",
                "error_code": exc.code,
                "validation_error": validation_message[:6000],
                "issues": exc.details.get("issues", []),
                "output_chars": len(output) if output else 0,
            }
            attempt_diagnostics.append(diagnostic)
            if on_validation_failure is not None:
                await on_validation_failure(attempt + 1, diagnostic)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TextProviderError(
                "ARK_TEXT_NETWORK_ERROR",
                "火山方舟文本服务暂时不可达",
                retryable=True,
                details={"request_id": request_id, "exception_type": type(exc).__name__},
            ) from exc
        except httpx.HTTPStatusError as exc:
            retryable = (
                exc.response.status_code in {408, 409, 425, 429} or exc.response.status_code >= 500
            )
            code = (
                "ARK_TEXT_AUTH_ERROR"
                if exc.response.status_code in {401, 403}
                else "ARK_TEXT_API_ERROR"
            )
            provider_error: dict[str, Any] = {}
            if provider_error_text:
                try:
                    parsed_error = json.loads(provider_error_text)
                    if isinstance(parsed_error, dict) and isinstance(
                        parsed_error.get("error"), dict
                    ):
                        provider_error = parsed_error["error"]
                except json.JSONDecodeError:
                    provider_error = {"message": provider_error_text[:300]}
            provider_summary = "：".join(
                str(provider_error[key]).strip()
                for key in ("code", "message")
                if str(provider_error.get(key, "")).strip()
            )
            message = f"火山方舟文本服务返回 HTTP {exc.response.status_code}"
            if provider_summary:
                message = f"{message}（{provider_summary[:300]}）"
            raise TextProviderError(
                code,
                message,
                retryable=retryable,
                details={
                    "request_id": request_id,
                    "status_code": exc.response.status_code,
                    "provider_error": provider_error,
                },
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            validation_message = str(exc)
            last_failure_kind = "schema"
            if not repair_source_json and output:
                repair_source_json = output
            diagnostic = {
                "attempt": attempt + 1,
                "request_id": request_id,
                "error_type": type(exc).__name__,
                "validation_error": validation_message[:4000],
                "output_chars": len(output) if "output" in locals() and output else 0,
            }
            attempt_diagnostics.append(diagnostic)
            if on_validation_failure is not None:
                await on_validation_failure(attempt + 1, diagnostic)
    if last_semantic_error is not None:
        raise TextProviderError(
            last_semantic_error.code,
            last_semantic_error.message,
            retryable=False,
            details={
                **last_semantic_error.details,
                "validator": validator.__name__,
                "attempts": attempt_diagnostics,
                "last_request_id": request_id,
            },
        )
    # 模型输出具有随机性，整轮换一次生成经常就能通过；允许任务层自动重试而不是直接失败
    raise TextProviderError(
        "ARK_TEXT_SCHEMA_INVALID",
        "火山方舟连续三次未返回符合创作合同的结构化 JSON",
        retryable=True,
        details={
            "validator": validator.__name__,
            "attempts": attempt_diagnostics,
            "last_request_id": request_id,
        },
    )


def _deterministic_excerpt_rewrite(
    selected_text: str,
    *,
    action: str,
    tone: str | None,
    custom_instruction: str | None,
) -> ScriptExcerptRewriteOutput:
    source = selected_text.strip()
    ending = source[-1] if source and source[-1] in "。！？!?…" else ""
    body = source[:-1] if ending else source

    if action == "SHORTEN":
        shortened = body
        for filler in ("我觉得", "其实", "真的", "可能", "也许", "就是说", "你知道"):
            shortened = shortened.replace(filler, "")
        clauses = [
            item.strip() for item in shortened.replace("；", "，").split("，") if item.strip()
        ]
        if len(clauses) > 1:
            shortened = "，".join(clauses[: max(1, (len(clauses) + 1) // 2)])
        elif len(shortened) > 8:
            shortened = shortened[: max(4, round(len(shortened) * 0.65))].rstrip("，、")
        rewritten = shortened + (ending or "。")
        rationale = "去掉铺垫和重复信息，保留这段话的核心动作。"
    elif action == "INTENSIFY_CONFLICT":
        rewritten = body.replace("请", "必须").replace("可以", "休想")
        if rewritten == body:
            rewritten = f"{body}——别再回避"
        rewritten = rewritten.rstrip("。！？!?") + "！"
        rationale = "提高措辞压力和对抗节奏，不改变人物与既有事实。"
    elif action == "ADJUST_TONE":
        target = tone or "克制"
        if target == "克制":
            rewritten = body.replace("必须", "需要").replace("绝对", "").rstrip("。！？!?") + "。"
        elif target == "强硬":
            rewritten = body.replace("请", "必须").rstrip("。！？!?") + "！"
        elif target == "温柔":
            rewritten = body.replace("必须", "希望").rstrip("。！？!?") + "，好吗？"
        elif target == "讽刺":
            rewritten = f"原来，{body.rstrip('。！？!?')}。"
        else:
            rewritten = body.rstrip("。！？!?") + "。"
        if rewritten == source:
            rewritten = f"{body}……"
        rationale = f"把表达调整为更{target}的语气，保留原意。"
    else:
        rewritten = body
        replacements = (
            ("但是", "可"),
            ("因为", "既然"),
            ("现在", "此刻"),
            ("不能", "不许"),
            ("不要", "别"),
        )
        for old, new in replacements:
            if old in rewritten:
                rewritten = rewritten.replace(old, new, 1)
                break
        if rewritten == body:
            rewritten = body.rstrip("。！？!?") + "……"
        else:
            rewritten += ending or "。"
        rationale = (
            f"按“{custom_instruction}”重写选中内容，并保留原有事实。"
            if action == "CUSTOM" and custom_instruction
            else "换一种更紧凑、自然的说法，保留原意。"
        )

    if rewritten == source:
        rewritten = source.rstrip("。！？!?…") + "……"
    return ScriptExcerptRewriteOutput(rewritten_text=rewritten, rationale=rationale)


async def generate_script_excerpt_rewrite(
    settings: Settings,
    *,
    selected_text: str,
    full_line: str,
    scene_context: str,
    action: str,
    tone: str | None = None,
    custom_instruction: str | None = None,
) -> TextGenerationResult:
    if not settings.ark_api_key:
        output = _deterministic_excerpt_rewrite(
            selected_text,
            action=action,
            tone=tone,
            custom_instruction=custom_instruction,
        )
        return TextGenerationResult(
            payload=output.model_dump(mode="json"),
            provider="mock",
            model="deterministic-script-rewriter-v1",
            request_id=None,
            repair_attempts=0,
        )

    action_labels = {
        "REWRITE": "改写，使表达更自然、具体、适合表演",
        "SHORTEN": "缩短，删除冗余但保留核心信息",
        "INTENSIFY_CONFLICT": "增强冲突，提高人物之间的压力与对抗",
        "ADJUST_TONE": f"把语气调整为“{tone}”",
        "CUSTOM": f"按用户要求改写：{custom_instruction}",
    }
    prompt = (
        "你是短剧台词编辑。只改写用户选中的片段，严格返回 JSON，不要 Markdown。"
        "不得改变人物身份、既有事实、人物关系、行动结果或叙事视角；"
        "不得凭空加入战神、赘婿、后宫、大女主等套路设定；"
        "不要把未选中的上下文重复到结果里。"
        f"\n任务：{action_labels[action]}"
        f"\n场景上下文：{scene_context}"
        f"\n完整台词：{full_line}"
        f"\n选中片段：{selected_text}"
        "\nrewritten_text 只能包含替换选中片段的新文字；"
        "rationale 用一句简短中文说明改动。"
        "\n输出必须符合 JSON Schema：\n"
        f"{json.dumps(ScriptExcerptRewriteOutput.model_json_schema(), ensure_ascii=False)}"
    )

    def validate_changed(candidate: BaseModel) -> None:
        rewritten = str(getattr(candidate, "rewritten_text", "")).strip()
        if rewritten == selected_text.strip():
            raise ModelOutputSemanticError(
                "SCRIPT_REWRITE_UNCHANGED",
                "模型没有改写选中内容",
                repair_message="rewritten_text 必须与原文不同，同时保持原意。",
                details={"selected_text": selected_text},
            )
        source_length = len(selected_text.strip())
        maximum_length = round(source_length * (2 if action == "CUSTOM" else 1.55)) + 4
        if len(rewritten) > maximum_length:
            raise ModelOutputSemanticError(
                "SCRIPT_REWRITE_OVEREXPANDED",
                "改写加入了过多新内容",
                repair_message=(
                    f"rewritten_text 不得超过 {maximum_length} 个字符。"
                    "删除新加入的背景、物品和事实，只保留选中片段原有信息。"
                ),
                details={
                    "selected_length": source_length,
                    "rewritten_length": len(rewritten),
                    "maximum_length": maximum_length,
                },
            )
        if action == "SHORTEN" and len(rewritten) >= source_length:
            raise ModelOutputSemanticError(
                "SCRIPT_REWRITE_NOT_SHORTER",
                "缩短结果没有比原文更短",
                repair_message="rewritten_text 必须更短，只保留核心信息。",
                details={
                    "selected_length": source_length,
                    "rewritten_length": len(rewritten),
                },
            )

    return await _ark_json(
        settings,
        prompt=prompt,
        validator=ScriptExcerptRewriteOutput,
        semantic_validator=validate_changed,
    )


class RoutedTextProvider:
    async def generate_directions(
        self,
        settings: Settings,
        brief: dict[str, Any],
        *,
        existing_results: dict[str, TextGenerationResult] | None = None,
        on_route_complete: Callable[[str, TextGenerationResult], Awaitable[None]] | None = None,
    ) -> TextGenerationResult:
        if not settings.ark_api_key:
            batch = deterministic_directions(brief)
            return TextGenerationResult(
                payload=batch.model_dump(mode="json"),
                provider="mock",
                model="deterministic-text-v2",
                request_id=None,
                repair_attempts=0,
            )

        async def generate_route(
            direction_key: str,
            label: str,
            emphasis: str,
        ) -> TextGenerationResult:
            recommended = direction_key == "market"
            expected_targeting = targeting_from_brief(brief)

            def restore_server_owned_targeting(payload: dict[str, Any]) -> dict[str, Any]:
                return {**payload, "narrative_targeting": expected_targeting}

            prompt = (
                "你是短剧总编剧。根据 Brief 只生成 1 个完整故事方向，严格返回 JSON，不要 Markdown。"
                f"direction_key 必须为 {direction_key}，本方向名称为“{label}”，"
                f"核心差异是：{emphasis}。"
                f"ai_recommendation.recommended 必须为 {str(recommended).lower()}。"
                f"方向必须遵循短剧公式：{SHORT_DRAMA_FORMULA}。"
                f"{targeting_prompt_guardrails(brief)}"
                "每个场景都要改变局面；爽点必须建立预期并及时兑现；"
                "反转必须回收前置线索、改变人物处境并升级下一冲突。"
                "方向总时长必须等于场景及镜头时长之和，并给出观众匹配、视觉标志、"
                "选择代价、3 至 5 个关键转折和制作风险。"
                "逐条检查 Brief 的 content_requirements 与 content_avoidances，"
                "在 brief_compliance 中原样带回每条要求并给出具体剧情证据。"
                "补充角色数、场景数、外景与特效要求，以及关键帧、视频和语音生成量。"
                "明确首集前 3 秒钩子、首个兑现点和结尾的具体剧情动作。"
                "结尾先完成当前小闭环，再以具体行动或事实揭示引出下一部冲突和目标。"
                "ending_hook 与 sequel_setup 禁止问号、互动 CTA、主题口号或对观众提问。"
                "输出必须符合此 JSON Schema：\n"
                f"{json.dumps(StoryDirection.model_json_schema(), ensure_ascii=False)}\nBrief:\n"
                f"{json.dumps(brief, ensure_ascii=False)}"
            )
            result = await _ark_json(
                settings,
                prompt=prompt,
                validator=StoryDirection,
                payload_normalizer=restore_server_owned_targeting,
            )
            direction = StoryDirection.model_validate(result.payload)
            # Cross-direction invariants are owned by the server, not left to three
            # independent model calls to coordinate implicitly.
            direction.direction_key = direction_key
            direction.ai_recommendation.recommended = recommended
            _enforce_generated_targeting(direction.model_dump(mode="json"), brief)
            return TextGenerationResult(
                payload=direction.model_dump(mode="json"),
                provider=result.provider,
                model=result.model,
                request_id=result.request_id,
                repair_attempts=result.repair_attempts,
            )

        results_by_key: dict[str, TextGenerationResult] = {}
        for direction_key, _, _ in DIRECTION_ROUTES:
            cached = (existing_results or {}).get(direction_key)
            if cached is None:
                continue
            direction = StoryDirection.model_validate(cached.payload)
            if direction.direction_key == direction_key:
                results_by_key[direction_key] = cached

        callback_lock = asyncio.Lock()

        async def generate_and_record(
            direction_key: str,
            label: str,
            emphasis: str,
        ) -> TextGenerationResult:
            result = await generate_route(direction_key, label, emphasis)
            if on_route_complete is not None:
                async with callback_lock:
                    await on_route_complete(direction_key, result)
            return result

        pending_routes = [
            (direction_key, label, emphasis)
            for direction_key, label, emphasis in DIRECTION_ROUTES
            if direction_key not in results_by_key
        ]
        tasks = [
            asyncio.create_task(generate_and_record(direction_key, label, emphasis))
            for direction_key, label, emphasis in pending_routes
        ]
        try:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        route_errors: dict[str, dict[str, Any]] = {}
        for (direction_key, _, _), result in zip(
            pending_routes, task_results, strict=True
        ):
            if isinstance(result, BaseException):
                if isinstance(result, TextProviderError):
                    route_errors[direction_key] = {
                        "code": result.code,
                        "message": str(result),
                        "retryable": result.retryable,
                        "details": result.details,
                    }
                else:
                    route_errors[direction_key] = {
                        "code": "ARK_TEXT_ROUTE_ERROR",
                        "message": str(result),
                        "retryable": True,
                        "details": {"exception_type": type(result).__name__},
                    }
                continue
            results_by_key[direction_key] = result

        if route_errors:
            completed_routes = [
                direction_key
                for direction_key, _, _ in DIRECTION_ROUTES
                if direction_key in results_by_key
            ]
            failed_routes = [
                direction_key
                for direction_key, _, _ in DIRECTION_ROUTES
                if direction_key in route_errors
            ]
            error_codes = {str(item["code"]) for item in route_errors.values()}
            code = (
                next(iter(error_codes))
                if not completed_routes and len(error_codes) == 1
                else "ARK_TEXT_PARTIAL_FAILURE"
            )
            primary_error = next(iter(route_errors.values()))
            primary_details = (
                dict(primary_error["details"])
                if not completed_routes and isinstance(primary_error["details"], dict)
                else {}
            )
            message = (
                f"3 个故事方向均未生成完成：{primary_error['message']}"
                if not completed_routes
                else (
                    f"3 个故事方向中已有 {len(completed_routes)} 个完成，"
                    f"{len(failed_routes)} 个失败；已保留成功结果，将只重试失败方向"
                )
            )
            raise TextProviderError(
                code,
                message,
                retryable=all(bool(item["retryable"]) for item in route_errors.values()),
                details={
                    **primary_details,
                    "completed_routes": completed_routes,
                    "failed_routes": failed_routes,
                    "failed_parts": failed_routes,
                    "completed_steps": [
                        f"故事方向 {direction_key}" for direction_key in completed_routes
                    ],
                    "route_errors": route_errors,
                },
            )

        results = [
            results_by_key[direction_key]
            for direction_key, _, _ in DIRECTION_ROUTES
        ]
        batch = StoryDirectionBatch.model_validate(
            {"directions": [result.payload for result in results]}
        )
        request_ids = [result.request_id for result in results if result.request_id]
        providers = {result.provider for result in results}
        models = {result.model for result in results}
        return TextGenerationResult(
            payload=batch.model_dump(mode="json"),
            provider=results[0].provider if len(providers) == 1 else "mixed",
            model=results[0].model if len(models) == 1 else "mixed",
            request_id=",".join(request_ids) or None,
            repair_attempts=sum(result.repair_attempts for result in results),
        )

    def _deterministic_package(
        self, brief: dict[str, Any], direction: dict[str, Any]
    ) -> StoryPackage:
        return deterministic_story_package(brief, direction)

    async def generate_story_structure(
        self,
        settings: Settings,
        brief: dict[str, Any],
        direction: dict[str, Any],
        *,
        on_model_output: Callable[[int], Awaitable[None]] | None = None,
        on_validation_failure: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> TextGenerationResult:
        if not settings.ark_api_key:
            structure = deterministic_story_structure(brief, direction)
            return TextGenerationResult(
                payload=structure.model_dump(mode="json"),
                provider="mock",
                model="deterministic-text-v3-relationship",
                request_id=None,
                repair_attempts=0,
            )
        locked_story_bible: dict[str, Any] | None = None
        locked_edges: list[dict[str, Any]] | None = None

        def edge_pair(edge: dict[str, Any]) -> tuple[str, str] | None:
            source = str(edge.get("source_character_key", ""))
            target = str(edge.get("target_character_key", ""))
            return tuple(sorted((source, target))) if source and target else None

        def normalize_with_locked_baseline(payload: dict[str, Any]) -> dict[str, Any]:
            normalized = normalize_story_structure_payload(payload, brief)
            if locked_story_bible is None or locked_edges is None:
                return normalized
            graph = normalized.get("relationship_graph")
            if not isinstance(graph, dict):
                return normalized

            locked_keys_by_pair = {
                pair: str(edge.get("relationship_key", ""))
                for edge in locked_edges
                if (pair := edge_pair(edge)) is not None
            }
            aliases = {
                str(edge.get("relationship_key", "")): locked_keys_by_pair[pair]
                for edge in graph.get("edges", [])
                if isinstance(edge, dict)
                and (pair := edge_pair(edge)) in locked_keys_by_pair
                and edge.get("relationship_key")
            }
            for beat in graph.get("beats", []):
                if not isinstance(beat, dict):
                    continue
                relationship_key = str(beat.get("relationship_key", ""))
                if relationship_key in aliases:
                    beat["relationship_key"] = aliases[relationship_key]

            normalized["story_bible"] = json.loads(
                json.dumps(locked_story_bible, ensure_ascii=False)
            )
            graph["edges"] = json.loads(json.dumps(locked_edges, ensure_ascii=False))
            graph["core_relationship_keys"] = [
                str(edge["relationship_key"])
                for edge in locked_edges
                if edge.get("is_core") is True and edge.get("relationship_key")
            ]
            return normalize_story_structure_payload(normalized, brief)

        def validate_and_lock_relationship_baseline(value: BaseModel) -> None:
            nonlocal locked_story_bible, locked_edges
            if not isinstance(value, StoryStructure):
                raise TypeError("角色关系语义校验只接受 StoryStructure")
            if locked_story_bible is None or locked_edges is None:
                locked_story_bible = value.story_bible.model_dump(mode="json")
                locked_edges = [
                    edge.model_dump(mode="json") for edge in value.relationship_graph.edges
                ]
            _validate_story_structure_relationships(value)

        prompt = (
            "你是短剧总编剧。只生成 Story Bible V2 与结构化角色关系网，不生成分集大纲或剧本。"
            f"{targeting_prompt_guardrails(brief)}"
            "Story Bible 只保存世界、规则、角色、伏笔和连续性，不得输出 relationships 自由文本。"
            "每个角色必须填写年龄（具体年龄或合理区间）、职业和 3 至 5 个性格关键词；"
            "关系网必须引用 Story Bible 中真实存在的 character key，至少包含一条核心冲突关系，"
            "当 relationship_types 包含 FAMILY 时必须填写 family_kinship：亲生、半血缘和双胞胎"
            "使用对应生物血缘类型；养父母、继父母和姻亲使用对应非血缘类型，不得把家庭气质"
            "直接标为遗传，并单独填写共同成长环境与经历。"
            "并通过 Relationship Beat 明确认证、揭示或选择导致的关系前后状态变化。"
            "逐条要求：每一条明面关系与真实关系不同的隐藏关系，都必须在 beats 中存在至少一条"
            "relationship_key 完全相同、trigger_type 为 REVEAL 或 AUTHENTICATION 的 Beat；"
            "Relationship Beat 的 trigger_ref 使用 authentication:N 格式回链认证步骤。"
            "输出前逐条自检 relationship_graph.edges，确认所有隐藏关系均有上述同 key 揭示计划，"
            "不得通过删除隐藏关系或把明面关系改成真实关系来规避校验。"
            "严格返回 JSON，不要 Markdown。输出必须符合此 JSON Schema：\n"
            f"{json.dumps(StoryStructure.model_json_schema(), ensure_ascii=False)}\nBrief:\n"
            f"{json.dumps(brief, ensure_ascii=False)}\nDirection:\n"
            f"{json.dumps(direction, ensure_ascii=False)}"
        )
        # 故事结构是全流程约束最强的单次生成（Story Bible + 关系网 + 跨字段一致性），
        # 关闭思考时 lite 模型极难一次性满足合同，开启深度思考显著提升结构化合规率
        result = await _ark_json(
            settings,
            prompt=prompt,
            validator=StoryStructure,
            payload_normalizer=normalize_with_locked_baseline,
            semantic_validator=validate_and_lock_relationship_baseline,
            on_validated_output=on_model_output,
            on_validation_failure=on_validation_failure,
            thinking_type="enabled",
        )
        _enforce_generated_targeting(result.payload, brief, nested_key="story_bible")
        return result

    async def generate_script_package(
        self,
        settings: Settings,
        brief: dict[str, Any],
        direction: dict[str, Any],
        story_bible: dict[str, Any],
        relationship_graph: dict[str, Any],
    ) -> TextGenerationResult:
        if not settings.ark_api_key:
            legacy_package = self._deterministic_package(brief, direction)
            package = apply_relationship_context_to_script_package(
                {
                    "outlines": [item.model_dump(mode="json") for item in legacy_package.outlines],
                    "scripts": [item.model_dump(mode="json") for item in legacy_package.scripts],
                    "critic": legacy_package.critic,
                },
                relationship_graph,
            )
            return TextGenerationResult(
                payload=package.model_dump(mode="json"),
                provider="mock",
                model="deterministic-text-v3-relationship",
                request_id=None,
                repair_attempts=0,
            )

        # 拆成大纲 / 剧本草稿 / 叙事引擎三阶段，避免单次超大 JSON 截断或漏字段后卡在 70%
        outlines = await self.generate_script_outlines(
            settings, brief, direction, story_bible, relationship_graph
        )
        foundation_context = {
            "story_bible": story_bible,
            "outlines": outlines.payload.get("outlines", []),
        }
        script = await self.generate_episode_script(
            settings, brief, direction, foundation_context
        )
        review = await self.generate_narrative_review(
            settings,
            brief,
            direction,
            script.payload,
            relationship_graph=relationship_graph,
        )
        assembled = assemble_script_package(outlines, script, review)
        _enforce_generated_targeting(assembled.payload, brief, require_contract=False)
        package = ScriptPackageOutput.model_validate(assembled.payload)
        try:
            package = apply_relationship_context_to_script_package(
                package.model_dump(mode="json"),
                relationship_graph,
            )
        except ValueError as exc:
            raise TextProviderError(
                "RELATIONSHIP_SCRIPT_CONTRACT_INVALID",
                str(exc),
                retryable=False,
                details={"request_id": assembled.request_id},
            ) from exc
        return TextGenerationResult(
            payload=package.model_dump(mode="json"),
            provider=assembled.provider,
            model=assembled.model,
            request_id=assembled.request_id,
            repair_attempts=assembled.repair_attempts,
        )

    async def generate_script_outlines(
        self,
        settings: Settings,
        brief: dict[str, Any],
        direction: dict[str, Any],
        story_bible: dict[str, Any],
        relationship_graph: dict[str, Any],
    ) -> TextGenerationResult:
        if not settings.ark_api_key:
            legacy_package = self._deterministic_package(brief, direction)
            outlines = ScriptPackageOutlines(
                outlines=legacy_package.outlines,
            )
            return TextGenerationResult(
                payload=outlines.model_dump(mode="json"),
                provider="mock",
                model="deterministic-text-v3-relationship",
                request_id=None,
                repair_attempts=0,
            )
        prompt = (
            "你是短剧总编剧。只基于已批准 Story Bible 与角色关系网生成分集大纲，"
            "不要生成剧本、叙事引擎或质检。"
            f"{targeting_prompt_guardrails(brief)}"
            "不得修改、替换或虚构新的角色关系。首集大纲必须存在，每集目标时长匹配 Brief。"
            "严格返回 JSON，不要 Markdown。输出必须符合此 JSON Schema：\n"
            f"{json.dumps(ScriptPackageOutlines.model_json_schema(), ensure_ascii=False)}\nBrief:\n"
            f"{json.dumps(brief, ensure_ascii=False)}\nDirection:\n"
            f"{json.dumps(direction, ensure_ascii=False)}\nApproved Story Bible:\n"
            f"{json.dumps(story_bible, ensure_ascii=False)}\nApproved Relationship Graph:\n"
            f"{json.dumps(relationship_graph, ensure_ascii=False)}"
        )
        return await _ark_json(
            settings,
            prompt=prompt,
            validator=ScriptPackageOutlines,
            thinking_type="enabled",
        )

    async def generate_story_foundation(
        self, settings: Settings, brief: dict[str, Any], direction: dict[str, Any]
    ) -> TextGenerationResult:
        if not settings.ark_api_key:
            package = self._deterministic_package(brief, direction)
            foundation = StoryFoundation(
                story_bible=package.story_bible,
                outlines=package.outlines,
            )
            return TextGenerationResult(
                payload=foundation.model_dump(mode="json"),
                provider="mock",
                model="deterministic-text-v2",
                request_id=None,
                repair_attempts=0,
            )
        prompt = (
            "你是短剧总编剧。只生成故事设定集和分集大纲，不生成剧本、节拍或质检。"
            f"{targeting_prompt_guardrails(brief)}"
            "人物、关系、伏笔和连续性规则必须可追溯；首集大纲必须存在，"
            "每个角色必须填写年龄（具体年龄或合理区间）、职业和 3 至 5 个性格关键词；"
            "每集目标时长严格匹配 Brief。严格返回 JSON，不要 Markdown。"
            "输出必须符合此 JSON Schema：\n"
            f"{json.dumps(StoryFoundation.model_json_schema(), ensure_ascii=False)}\nBrief:\n"
            f"{json.dumps(brief, ensure_ascii=False)}\nDirection:\n"
            f"{json.dumps(direction, ensure_ascii=False)}"
        )
        result = await _ark_json(settings, prompt=prompt, validator=StoryFoundation)
        _enforce_generated_targeting(result.payload, brief, nested_key="story_bible")
        return result

    async def generate_episode_script(
        self,
        settings: Settings,
        brief: dict[str, Any],
        direction: dict[str, Any],
        foundation: dict[str, Any],
    ) -> TextGenerationResult:
        if not settings.ark_api_key:
            script = self._deterministic_package(brief, direction).scripts[0]
            draft = EpisodeScriptDraft(
                episode_ordinal=1,
                title=script.title,
                canonical_language=script.canonical_language,
                scenes=script.scenes,
            )
            return TextGenerationResult(
                payload=draft.model_dump(mode="json"),
                provider="mock",
                model="deterministic-text-v2",
                request_id=None,
                repair_attempts=0,
            )
        prompt = (
            "你是短剧编剧。只生成首集结构化场景和台词，不生成 Story Bible、分集大纲、"
            f"{targeting_prompt_guardrails(brief)}"
            "叙事引擎或质检。首集必须有 2 至 6 个场景，每个场景都改变故事状态；"
            "每个场景 lines 至少 1 条，禁止空数组。"
            "场景时长总和必须匹配 Brief 目标，服务端会确定性计算总时长。"
            "严格返回 JSON，不要 Markdown。输出必须符合此 JSON Schema：\n"
            f"{json.dumps(EpisodeScriptDraft.model_json_schema(), ensure_ascii=False)}\nBrief:\n"
            f"{json.dumps(brief, ensure_ascii=False)}\nDirection:\n"
            f"{json.dumps(direction, ensure_ascii=False)}\nStory Foundation:\n"
            f"{json.dumps(foundation, ensure_ascii=False)}"
        )
        result = await _ark_json(
            settings,
            prompt=prompt,
            validator=EpisodeScriptDraft,
            payload_normalizer=normalize_episode_script_draft,
            thinking_type="enabled",
        )
        _enforce_generated_targeting(result.payload, brief, require_contract=False)
        return result

    async def generate_narrative_review(
        self,
        settings: Settings,
        brief: dict[str, Any],
        direction: dict[str, Any],
        script: dict[str, Any],
        relationship_graph: dict[str, Any] | None = None,
    ) -> TextGenerationResult:
        if not settings.ark_api_key:
            package_script = self._deterministic_package(brief, direction).scripts[0]
            review = NarrativeReview(
                short_drama_engine=package_script.short_drama_engine,
                breakout_engine=package_script.breakout_engine,
                critic=self._deterministic_package(brief, direction).critic,
            )
            return TextGenerationResult(
                payload=review.model_dump(mode="json"),
                provider="mock",
                model="deterministic-text-v2",
                request_id=None,
                repair_attempts=0,
            )
        relationship_guard = ""
        relationship_block = ""
        if relationship_graph is not None:
            relationship_guard = (
                "不得修改、替换或虚构新的角色关系。"
                "breakout_engine.relationship_reorders 必须引用批准关系网中的 relationship_key；"
                "trigger_auth_sequence 必须与关系变化 trigger_ref 中的 authentication:N 一致；"
                "关系变化除情绪外，必须改变行动关系、权力、信任、冲突或目标。"
            )
            relationship_block = (
                "\nApproved Relationship Graph:\n"
                f"{json.dumps(relationship_graph, ensure_ascii=False)}"
            )
        prompt = (
            "你是短剧主编与质检。只基于已完成的首集剧本生成两套叙事引擎和 critic。"
            f"{targeting_prompt_guardrails(brief)}"
            f"{relationship_guard}"
            f"短剧引擎必须落实：{SHORT_DRAMA_FORMULA}；至少两级递进式因果反转，"
            "节拍必须包含 HOOK、ESCALATION、PAYOFF、CLOSURE、CONTINUATION_HOOK，"
            "最后一个节拍必须是续作悬念。"
            f"爆款引擎必须落实：{BREAKOUT_FORMULA}；误判、认证与关系重排必须有因果关系。"
            "服务端会统一时长、节拍时间、场景引用，以及两个引擎间重复的闭环和续作字段。"
            "严格返回 JSON，不要 Markdown。输出必须符合此 JSON Schema：\n"
            f"{json.dumps(NarrativeReview.model_json_schema(), ensure_ascii=False)}\nBrief:\n"
            f"{json.dumps(brief, ensure_ascii=False)}\nDirection:\n"
            f"{json.dumps(direction, ensure_ascii=False)}\nEpisode Script:\n"
            f"{json.dumps(script, ensure_ascii=False)}"
            f"{relationship_block}"
        )
        result = await _ark_json(
            settings,
            prompt=prompt,
            validator=NarrativeReview,
            thinking_type="enabled",
        )
        _enforce_generated_targeting(result.payload, brief, require_contract=False)
        return result

    async def generate_story_package(
        self, settings: Settings, brief: dict[str, Any], direction: dict[str, Any]
    ) -> TextGenerationResult:
        foundation = await self.generate_story_foundation(settings, brief, direction)
        script = await self.generate_episode_script(settings, brief, direction, foundation.payload)
        review = await self.generate_narrative_review(settings, brief, direction, script.payload)
        return assemble_story_package(foundation, script, review)
