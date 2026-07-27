from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DirectorIntentChannel = Literal[
    "NARRATIVE",
    "CAMERA",
    "PERFORMANCE",
    "SOUND",
    "PACING",
]
DirectorIntentConsumer = Literal["STORYBOARD", "PROMPT", "AUDIO", "TIMELINE"]

_REQUIRED_CHANNELS = {
    "NARRATIVE",
    "CAMERA",
    "PERFORMANCE",
    "SOUND",
    "PACING",
}
_REQUIRED_CONSUMERS = {"STORYBOARD", "PROMPT", "AUDIO", "TIMELINE"}


class DirectorIntentEntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal[
        "StoryBeat",
        "CharacterGoal",
        "ScriptScene",
        "Scene",
        "Shot",
        "Timeline",
    ]
    id: str = Field(min_length=1, max_length=120)
    version_id: str | None = Field(default=None, min_length=1, max_length=120)
    field_path: str | None = Field(default=None, min_length=1, max_length=240)
    content_hash: str | None = Field(default=None, min_length=8, max_length=128)


class DirectorIntentTimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "DirectorIntentTimeRange":
        if self.end_ms <= self.start_ms:
            raise ValueError("导演意图时间范围的 end_ms 必须大于 start_ms")
        return self


class DirectorIntentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1, max_length=80)
    source: DirectorIntentEntityRef
    claim: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    time_range: DirectorIntentTimeRange | None = None


class DirectorIntentCandidateTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target: DirectorIntentEntityRef
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)


class DirectorIntentScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resolution_status: Literal["RESOLVED", "AMBIGUOUS", "UNRESOLVED"]
    scene: DirectorIntentEntityRef
    plot_beat: DirectorIntentEntityRef | None = None
    character_goals: list[DirectorIntentEntityRef] = Field(default_factory=list, max_length=12)
    time_range: DirectorIntentTimeRange | None = None
    candidate_targets: list[DirectorIntentCandidateTarget] = Field(
        default_factory=list,
        max_length=8,
    )
    resolution_reason: str = Field(min_length=1, max_length=1000)
    context_fingerprint: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def resolved_scope_requires_semantic_targets(self) -> "DirectorIntentScope":
        if self.resolution_status == "RESOLVED":
            if self.plot_beat is None:
                raise ValueError("已解析的导演意图必须绑定 plot_beat")
            if not self.character_goals:
                raise ValueError("已解析的导演意图必须绑定至少一个 character_goal")
            if self.time_range is None:
                raise ValueError("已解析的导演意图必须绑定 time_range")
        if self.resolution_status == "AMBIGUOUS" and len(self.candidate_targets) < 2:
            raise ValueError("歧义作用域必须提供至少两个候选目标")
        return self


class DirectorIntentDirective(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    channel: DirectorIntentChannel
    status: Literal["CHANGE", "PRESERVE", "UNRESOLVED"]
    instruction: str = Field(min_length=1, max_length=1200)
    observable_effect: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)


class DirectorIntentConflictCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Z0-9_]+$")
    category: Literal[
        "CHARACTER_LOCK",
        "WORLD_RULE",
        "STORY_CAUSALITY",
        "SCOPE",
        "TIMING",
        "CROSS_CHANNEL",
        "TERM_GROUNDING",
    ]
    severity: Literal["BLOCKING", "WARNING", "INFO"]
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    message: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class DirectorIntentInheritanceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    consumer: DirectorIntentConsumer
    status: Literal["PENDING", "NOT_APPLICABLE"]
    inherited_fields: list[DirectorIntentChannel] = Field(default_factory=list, max_length=5)
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def not_applicable_requires_reason(self) -> "DirectorIntentInheritanceTarget":
        if self.status == "NOT_APPLICABLE" and self.reason is None:
            raise ValueError("NOT_APPLICABLE 的继承目标必须说明原因")
        return self


class DirectorIntent(BaseModel):
    """A versioned intent attachment over canonical state, never a second write model."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["director-intent-v1"] = "director-intent-v1"
    intent_id: str = Field(min_length=36, max_length=36)
    intent_version: int = Field(ge=1)
    project_id: str = Field(min_length=36, max_length=36)
    source_request: str = Field(min_length=1, max_length=2000)
    source_request_language: str = Field(default="zh-CN", min_length=2, max_length=20)
    scope: DirectorIntentScope
    evidence: list[DirectorIntentEvidence] = Field(min_length=1, max_length=80)
    directives: list[DirectorIntentDirective] = Field(min_length=5, max_length=5)
    rationale: str = Field(min_length=1, max_length=2000)
    overall_confidence: float = Field(ge=0, le=1)
    conflict_checks: list[DirectorIntentConflictCheck] = Field(min_length=1, max_length=40)
    inheritance_targets: list[DirectorIntentInheritanceTarget] = Field(
        min_length=4,
        max_length=4,
    )
    state: Literal["DRAFT", "PREVIEW_READY", "BLOCKED", "CONFIRMED", "STALE"]
    can_confirm: bool
    blocked_reasons: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_confirmation_contract(self) -> "DirectorIntent":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("导演意图 evidence_id 不能重复")
        known_evidence = set(evidence_ids)
        unknown_evidence = {
            ref
            for directive in self.directives
            for ref in directive.evidence_refs
            if ref not in known_evidence
        }
        unknown_evidence.update(
            ref
            for check in self.conflict_checks
            for ref in check.evidence_refs
            if ref not in known_evidence
        )
        if unknown_evidence:
            raise ValueError(f"导演意图引用了不存在的证据：{sorted(unknown_evidence)}")

        channels = [item.channel for item in self.directives]
        if len(channels) != len(set(channels)) or set(channels) != _REQUIRED_CHANNELS:
            raise ValueError("导演意图必须且只能包含五个专业通道")
        consumers = [item.consumer for item in self.inheritance_targets]
        if len(consumers) != len(set(consumers)) or set(consumers) != _REQUIRED_CONSUMERS:
            raise ValueError("导演意图必须声明分镜、提示词、音频和时间线继承目标")

        blocking_conflict = any(
            item.severity == "BLOCKING" and item.status != "PASS"
            for item in self.conflict_checks
        )
        unresolved_directive = any(item.status == "UNRESOLVED" for item in self.directives)
        eligible = (
            self.scope.resolution_status == "RESOLVED"
            and self.overall_confidence >= 0.65
            and not blocking_conflict
            and not unresolved_directive
        )
        if self.can_confirm != eligible:
            raise ValueError("can_confirm 与作用域、置信度、冲突和通道解析结果不一致")
        if self.can_confirm and self.blocked_reasons:
            raise ValueError("可确认意图不能包含 blocked_reasons")
        if not self.can_confirm and not self.blocked_reasons:
            raise ValueError("不可确认意图必须给出 blocked_reasons")
        if self.state == "CONFIRMED" and not self.can_confirm:
            raise ValueError("不可确认意图不能进入 CONFIRMED")
        return self


class DirectorIntentPreviewSection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    channel: DirectorIntentChannel
    before: str = Field(min_length=1, max_length=1200)
    after: str = Field(min_length=1, max_length=1200)
    why: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)


class DirectorIntentChangePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["director-intent-change-preview-v1"] = (
        "director-intent-change-preview-v1"
    )
    projection_mode: Literal["READ_ONLY"] = "READ_ONLY"
    canonical_source: Literal["FILM_IR"] = "FILM_IR"
    intent: DirectorIntent
    sections: list[DirectorIntentPreviewSection] = Field(min_length=5, max_length=5)
    preserved_invariants: list[str] = Field(min_length=1, max_length=30)
    downstream_summary: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_preview(self) -> "DirectorIntentChangePreview":
        channels = [item.channel for item in self.sections]
        if len(channels) != len(set(channels)) or set(channels) != _REQUIRED_CHANNELS:
            raise ValueError("变更预览必须且只能展示五个专业通道")
        known_evidence = {item.evidence_id for item in self.intent.evidence}
        unknown_evidence = {
            ref
            for section in self.sections
            for ref in section.evidence_refs
            if ref not in known_evidence
        }
        if unknown_evidence:
            raise ValueError(f"变更预览引用了不存在的证据：{sorted(unknown_evidence)}")
        return self
