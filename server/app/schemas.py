import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import (
    AliasPath,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.domain.narrative_targeting import (
    EmotionalReward,
    NarrativeProtagonist,
    ProductionFormat,
    TargetAudience,
)


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectRead(OrmModel):
    id: str
    name: str
    idea: str
    genre: str
    style: str
    target_duration_sec: int
    aspect_ratio: str
    target_platform: str
    status: str
    lock_version: int
    available_points: int
    timeline_version: int
    preview_approved: bool
    export_ready: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_utc(self, value: datetime) -> str:
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class PlatformTarget(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    platform: str = Field(min_length=1, max_length=40)
    priority: Literal["PRIMARY", "SECONDARY"]
    aspect_ratio: Literal["9:16", "16:9"]
    target_duration_sec: int = Field(ge=45, le=90)
    caption_mode: Literal["BURNED_IN", "SIDECAR", "BOTH"] = "BOTH"


class ExportProfileCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    platform: str = Field(min_length=1, max_length=40)
    aspect_ratio: Literal["9:16", "16:9"]
    width: int = Field(ge=360, le=3840)
    height: int = Field(ge=360, le=3840)
    caption_mode: Literal["BURNED_IN", "SIDECAR", "BOTH"] = "BOTH"
    languages: list[str] = Field(min_length=1, max_length=12)
    audio_tracks: list[str] = Field(
        default_factory=lambda: ["DIALOGUE", "BGM", "AMBIENCE", "SFX"],
        max_length=8,
    )
    watermark: dict[str, str | int | float | bool] = Field(default_factory=dict)
    actor: str = Field(default="local-user", min_length=1, max_length=80)


class ExportMatrixRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    profile_ids: list[str] = Field(min_length=1, max_length=8)
    languages: list[str] = Field(min_length=1, max_length=12)
    actor: str = Field(default="local-user", min_length=1, max_length=80)


def _require_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label}不能重复")


def _validate_targeting(
    *,
    primary_audience: str | None,
    secondary_audiences: list[str] | None,
    primary_market: str | None,
    secondary_markets: list[str] | None,
    canonical_language: str | None,
    localization_targets: list[str] | None,
) -> None:
    if secondary_audiences is not None:
        _require_unique(secondary_audiences, "次要目标用户")
        if primary_audience is not None and primary_audience in secondary_audiences:
            raise ValueError("主目标用户不能同时出现在次要目标用户中")
    if secondary_markets is not None:
        _require_unique(secondary_markets, "次要市场")
        if primary_market is not None and primary_market in secondary_markets:
            raise ValueError("主市场不能同时出现在次要市场中")
    if localization_targets is not None:
        _require_unique(localization_targets, "本地化语言")
        if canonical_language is not None and canonical_language in localization_targets:
            raise ValueError("规范语言不能同时出现在本地化语言中")


def _validate_platform_targets(values: list[PlatformTarget]) -> PlatformTarget:
    if not values:
        raise ValueError("至少需要一个目标平台")
    _require_unique([item.platform for item in values], "目标平台")
    primary = [item for item in values if item.priority == "PRIMARY"]
    if len(primary) != 1:
        raise ValueError("目标平台必须且只能有一个 PRIMARY")
    return primary[0]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    idea: str = Field(min_length=10, max_length=4000)
    genre: str = Field(default="urban_drama", min_length=1, max_length=80)
    style: str = Field(default="realistic_cinematic", min_length=1, max_length=80)
    target_duration_sec: int = Field(default=60, ge=45, le=90)
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    target_platform: str = Field(default="douyin", min_length=1, max_length=40)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    narrative_protagonist: NarrativeProtagonist = "unspecified"
    target_audience: TargetAudience = "general"
    emotional_rewards: list[EmotionalReward] = Field(default_factory=list, max_length=7)
    audience_profile: str = Field(default="", max_length=240)
    production_format: ProductionFormat = "live_action"
    primary_audience: str = Field(default="general", min_length=1, max_length=80)
    secondary_audiences: list[str] = Field(default_factory=list, max_length=12)
    primary_market: str = Field(default="CN", min_length=2, max_length=16)
    secondary_markets: list[str] = Field(default_factory=list, max_length=12)
    canonical_language: str = Field(default="zh-CN", min_length=2, max_length=24)
    localization_targets: list[str] = Field(default_factory=list, max_length=12)
    platform_targets: list[PlatformTarget] = Field(default_factory=list, max_length=8)
    content_requirements: list[str] = Field(default_factory=list, max_length=30)
    content_avoidances: list[str] = Field(default_factory=list, max_length=30)
    creative_defaults: dict[str, str | int | float | bool] = Field(default_factory=dict)
    blocking_questions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("emotional_rewards")
    @classmethod
    def unique_emotional_rewards(cls, values: list[EmotionalReward]) -> list[EmotionalReward]:
        _require_unique(values, "情绪回报")
        return values

    @model_validator(mode="after")
    def normalize_and_validate_targets(self) -> "ProjectCreate":
        _validate_targeting(
            primary_audience=self.primary_audience,
            secondary_audiences=self.secondary_audiences,
            primary_market=self.primary_market,
            secondary_markets=self.secondary_markets,
            canonical_language=self.canonical_language,
            localization_targets=self.localization_targets,
        )
        if not self.platform_targets:
            self.platform_targets = [
                PlatformTarget(
                    platform=self.target_platform,
                    priority="PRIMARY",
                    aspect_ratio=self.aspect_ratio,
                    target_duration_sec=self.target_duration_sec,
                )
            ]
        primary = _validate_platform_targets(self.platform_targets)
        self.target_platform = primary.platform
        self.aspect_ratio = primary.aspect_ratio
        self.target_duration_sec = primary.target_duration_sec
        return self


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    idea: str | None = Field(default=None, min_length=10, max_length=4000)
    genre: str | None = Field(default=None, min_length=1, max_length=80)
    style: str | None = Field(default=None, min_length=1, max_length=80)
    target_duration_sec: int | None = Field(default=None, ge=45, le=90)
    aspect_ratio: Literal["9:16", "16:9"] | None = None
    target_platform: str | None = Field(default=None, min_length=1, max_length=40)
    reference_asset_ids: list[str] | None = Field(default=None, max_length=20)
    assumptions: list[str] | None = Field(default=None, max_length=20)
    narrative_protagonist: NarrativeProtagonist | None = None
    target_audience: TargetAudience | None = None
    emotional_rewards: list[EmotionalReward] | None = Field(default=None, max_length=7)
    audience_profile: str | None = Field(default=None, max_length=240)
    production_format: ProductionFormat | None = None
    primary_audience: str | None = Field(default=None, min_length=1, max_length=80)
    secondary_audiences: list[str] | None = Field(default=None, max_length=12)
    primary_market: str | None = Field(default=None, min_length=2, max_length=16)
    secondary_markets: list[str] | None = Field(default=None, max_length=12)
    canonical_language: str | None = Field(default=None, min_length=2, max_length=24)
    localization_targets: list[str] | None = Field(default=None, max_length=12)
    platform_targets: list[PlatformTarget] | None = Field(default=None, max_length=8)
    content_requirements: list[str] | None = Field(default=None, max_length=30)
    content_avoidances: list[str] | None = Field(default=None, max_length=30)
    creative_defaults: dict[str, str | int | float | bool] | None = None
    blocking_questions: list[str] | None = Field(default=None, max_length=20)

    @field_validator("emotional_rewards")
    @classmethod
    def unique_updated_emotional_rewards(
        cls, values: list[EmotionalReward] | None
    ) -> list[EmotionalReward] | None:
        if values is not None:
            _require_unique(values, "情绪回报")
        return values

    @model_validator(mode="after")
    def require_edit(self) -> "ProjectUpdate":
        editable = self.model_dump(exclude={"expected_version"}, exclude_none=True)
        if not editable:
            raise ValueError("至少提交一个可编辑字段")
        _validate_targeting(
            primary_audience=self.primary_audience,
            secondary_audiences=self.secondary_audiences,
            primary_market=self.primary_market,
            secondary_markets=self.secondary_markets,
            canonical_language=self.canonical_language,
            localization_targets=self.localization_targets,
        )
        if self.platform_targets is not None:
            primary = _validate_platform_targets(self.platform_targets)
            if self.target_platform is not None and self.target_platform != primary.platform:
                raise ValueError("target_platform 必须与 PRIMARY 平台一致")
        return self


class ProjectCreateResult(BaseModel):
    project: ProjectRead
    brief_version: int
    idempotency_replayed: bool


class ProjectUpdateResult(BaseModel):
    project: ProjectRead
    brief_version: int


class ProjectNameSuggestionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    current_name: str | None = Field(default=None, max_length=120)
    idea: str = Field(min_length=10, max_length=4000)
    genre: str = Field(min_length=1, max_length=80)
    style: str = Field(min_length=1, max_length=80)
    narrative_protagonist: NarrativeProtagonist = "unspecified"
    target_audience: TargetAudience = "general"
    emotional_rewards: list[EmotionalReward] = Field(default_factory=list, max_length=7)
    audience_profile: str = Field(default="", max_length=240)
    production_format: ProductionFormat = "live_action"
    primary_market: str = Field(default="CN", min_length=2, max_length=16)
    canonical_language: str = Field(default="zh-CN", min_length=2, max_length=24)
    target_duration_sec: int = Field(default=60, ge=45, le=90)
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    target_platform: str = Field(default="douyin", min_length=1, max_length=40)
    content_requirements: list[str] = Field(default_factory=list, max_length=40)
    content_avoidances: list[str] = Field(default_factory=list, max_length=40)


class ProjectNameSuggestionRead(BaseModel):
    original: str | None
    suggested: str
    provider: str
    model: str
    warning: str | None = None


class BriefRequirementsSuggestionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    idea: str = Field(min_length=10, max_length=4000)
    genre: str = Field(min_length=1, max_length=80)
    style: str = Field(min_length=1, max_length=80)
    target_duration_sec: int = Field(ge=45, le=90)
    aspect_ratio: Literal["9:16", "16:9"]
    target_platform: str = Field(min_length=1, max_length=40)
    narrative_protagonist: NarrativeProtagonist = "unspecified"
    target_audience: TargetAudience = "general"
    emotional_rewards: list[EmotionalReward] = Field(default_factory=list, max_length=7)
    audience_profile: str = Field(default="", max_length=240)
    production_format: ProductionFormat = "live_action"
    primary_market: str = Field(default="CN", min_length=2, max_length=16)
    canonical_language: str = Field(default="zh-CN", min_length=2, max_length=24)
    existing_requirements: list[str] = Field(default_factory=list, max_length=30)
    content_avoidances: list[str] = Field(default_factory=list, max_length=30)


class BriefRequirementsSuggestionRead(BaseModel):
    items: list[str]
    provider: str
    model: str
    warning: str | None = None


class BriefAvoidancesSuggestionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    idea: str = Field(min_length=10, max_length=4000)
    genre: str = Field(min_length=1, max_length=80)
    style: str = Field(min_length=1, max_length=80)
    target_duration_sec: int = Field(ge=45, le=90)
    aspect_ratio: Literal["9:16", "16:9"]
    target_platform: str = Field(min_length=1, max_length=40)
    narrative_protagonist: NarrativeProtagonist = "unspecified"
    target_audience: TargetAudience = "general"
    emotional_rewards: list[EmotionalReward] = Field(default_factory=list, max_length=7)
    audience_profile: str = Field(default="", max_length=240)
    production_format: ProductionFormat = "live_action"
    primary_market: str = Field(default="CN", min_length=2, max_length=16)
    canonical_language: str = Field(default="zh-CN", min_length=2, max_length=24)
    content_requirements: list[str] = Field(default_factory=list, max_length=30)
    existing_avoidances: list[str] = Field(default_factory=list, max_length=30)


class BriefAvoidancesSuggestionRead(BaseModel):
    items: list[str]
    provider: str
    model: str
    warning: str | None = None


class BriefBlockingQuestionsSuggestionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    idea: str = Field(min_length=10, max_length=4000)
    genre: str = Field(min_length=1, max_length=80)
    style: str = Field(min_length=1, max_length=80)
    target_duration_sec: int = Field(ge=45, le=90)
    aspect_ratio: Literal["9:16", "16:9"]
    target_platform: str = Field(min_length=1, max_length=40)
    narrative_protagonist: NarrativeProtagonist = "unspecified"
    target_audience: TargetAudience = "general"
    emotional_rewards: list[EmotionalReward] = Field(default_factory=list, max_length=7)
    audience_profile: str = Field(default="", max_length=240)
    production_format: ProductionFormat = "live_action"
    primary_market: str = Field(default="CN", min_length=2, max_length=16)
    canonical_language: str = Field(default="zh-CN", min_length=2, max_length=24)
    content_requirements: list[str] = Field(default_factory=list, max_length=30)
    content_avoidances: list[str] = Field(default_factory=list, max_length=30)
    existing_questions: list[str] = Field(default_factory=list, max_length=20)


class BriefBlockingQuestionsSuggestionRead(BaseModel):
    items: list[str]
    provider: str
    model: str
    warning: str | None = None


class BriefStoryRewriteRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    idea: str = Field(min_length=10, max_length=4000)
    genre: str = Field(min_length=1, max_length=80)
    style: str = Field(min_length=1, max_length=80)
    target_duration_sec: int = Field(ge=45, le=90)
    aspect_ratio: Literal["9:16", "16:9"]
    target_platform: str = Field(min_length=1, max_length=40)
    secondary_platforms: list[str] = Field(default_factory=list, max_length=10)
    narrative_protagonist: NarrativeProtagonist = "unspecified"
    target_audience: TargetAudience = "general"
    emotional_rewards: list[EmotionalReward] = Field(default_factory=list, max_length=7)
    audience_profile: str = Field(default="", max_length=240)
    production_format: ProductionFormat = "live_action"
    primary_market: str = Field(default="CN", min_length=2, max_length=16)
    secondary_markets: list[str] = Field(default_factory=list, max_length=20)
    canonical_language: str = Field(default="zh-CN", min_length=2, max_length=24)
    localization_targets: list[str] = Field(default_factory=list, max_length=20)
    content_requirements: list[str] = Field(default_factory=list, max_length=30)
    content_avoidances: list[str] = Field(default_factory=list, max_length=30)


class BriefStoryRewriteRead(BaseModel):
    original: str
    rewritten: str
    logic_checks: list[str]
    provider: str
    model: str


class ProjectSummary(ProjectRead):
    episode_count: int
    scene_count: int
    shot_count: int


class ProjectStageRead(BaseModel):
    key: str
    label: str
    status: Literal["COMPLETE", "CURRENT", "IN_PROGRESS", "BLOCKED", "LOCKED"]
    href: str
    detail: str


class ProjectReadinessBlockerRead(BaseModel):
    code: str
    message: str
    action_label: str
    action_href: str


class ProjectReadinessRead(BaseModel):
    project_id: str
    workflow_mode: Literal["CLASSIC", "PIPELINE", "HYBRID"]
    project_status: str
    summary_status: Literal["READY", "IN_PROGRESS", "ACTION_REQUIRED", "BLOCKED"]
    active_stage_key: str
    active_job_count: int
    stages: list[ProjectStageRead]
    blockers: list[ProjectReadinessBlockerRead]
    next_action_label: str
    next_action_href: str
    updated_at: datetime


class BriefVersionRead(BaseModel):
    id: str
    project_id: str
    version: int
    project_name: str
    raw_input: str
    genre: str
    style: str
    target_duration_sec: int
    aspect_ratio: str
    target_platform: str
    reference_asset_ids: list[str]
    assumptions: list[str]
    narrative_protagonist: NarrativeProtagonist
    target_audience: TargetAudience
    emotional_rewards: list[EmotionalReward]
    audience_profile: str
    production_format: ProductionFormat
    primary_audience: str
    secondary_audiences: list[str]
    primary_market: str
    secondary_markets: list[str]
    canonical_language: str
    localization_targets: list[str]
    platform_targets: list[PlatformTarget]
    content_requirements: list[str]
    content_avoidances: list[str]
    creative_defaults: dict[str, str | int | float | bool]
    blocking_questions: list[str]
    payload_schema_version: str
    content_hash: str
    status: str
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class EpisodeRead(OrmModel):
    id: str
    project_id: str
    code: str
    title: str
    target_duration_sec: int
    status: str


class SceneRead(OrmModel):
    id: str
    episode_id: str
    code: str
    ordinal: int
    title: str
    purpose: str
    duration_sec: int
    status: str


class ShotCharacterBindingRead(BaseModel):
    id: str
    name: str
    role: str
    visual_brief: str
    look_version: str
    locked_candidate_id: str
    reference_asset_id: str
    reference_asset_url: str
    identity_version_id: str | None = None
    look_version_id: str | None = None
    story_state_version_id: str | None = None


class IdentityReviewRecord(BaseModel):
    decision: str
    issues: list[str] = Field(default_factory=list)
    note: str | None = None
    actor: str
    reviewed_at: datetime
    score: float | None = None
    reference_asset_ids: list[str] = Field(default_factory=list)
    look_version: str | None = None


class ShotRead(OrmModel):
    id: str
    scene_id: str
    code: str
    ordinal: int
    title: str
    description: str
    dialogue: str
    duration_sec: int
    status: str
    shot_size: str
    camera_movement: str
    current_take: int
    candidate_take: int | None
    continuity: str
    location: str
    time_of_day: str
    lock_version: int
    character_ids: list[str] = Field(default_factory=list)
    character_look_version: str = "Look V1"
    character_identity_version_ids: list[str] = Field(default_factory=list)
    character_look_version_ids: list[str] = Field(default_factory=list)
    character_story_state_version_ids: list[str] = Field(default_factory=list)
    character_bindings: list[ShotCharacterBindingRead] = Field(default_factory=list)
    current_image_url: str | None = None
    candidate_image_url: str | None = None
    current_image_model: str | None = None
    candidate_image_model: str | None = None
    current_video_url: str | None = None
    candidate_video_url: str | None = None
    current_identity_status: str | None = None
    candidate_identity_status: str | None = None
    candidate_identity_score: float | None = None
    candidate_identity_message: str | None = None
    current_identity_review: IdentityReviewRecord | None = None
    candidate_identity_review: IdentityReviewRecord | None = None
    latest_identity_review: IdentityReviewRecord | None = None


class ShotImageGenerateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    prompt: str | None = Field(default=None, min_length=3, max_length=4000)
    model: str | None = Field(default=None, min_length=3, max_length=160)
    resolution: Literal["1K", "2K", "3K", "4K"] = "2K"
    aspect_ratio: Literal["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9"] | None = None


class ShotCharacterBindingUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    character_ids: list[str] = Field(default_factory=list, max_length=8)
    look_version: str = Field(default="Look V1", min_length=1, max_length=40)


class LegacyIdentityReviewRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class IdentityReviewRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    decision: Literal["APPROVE_AND_APPLY", "REGENERATE", "OVERRIDE_AND_APPLY"]
    issues: list[
        Literal[
            "FACE_SHAPE",
            "FACIAL_FEATURES",
            "HAIR",
            "AGE_IMPRESSION",
            "WARDROBE",
            "BODY_PROPORTIONS",
            "SIGNATURE_ACCESSORIES",
        ]
    ] = Field(default_factory=list, max_length=7)
    note: str | None = Field(default=None, max_length=1000)
    expected_version: int = Field(ge=1)
    actor: str = Field(default="创作者", min_length=1, max_length=80)


class PromptEnhanceRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(min_length=3, max_length=4000)


class PromptEnhanceRead(BaseModel):
    original: str
    enhanced: str
    provider: str
    model: str
    warning: str | None = None


class ShotVideoGenerateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    prompt: str | None = Field(default=None, min_length=3, max_length=1200)
    image_url: AnyHttpUrl | None = None
    duration: int = Field(default=5, ge=5, le=10)
    camera_fixed: bool = False
    watermark: bool = True


class JobRead(OrmModel):
    id: str
    project_id: str
    project_name: str = Field(validation_alias=AliasPath("project", "name"))
    job_type: str
    entity_type: str
    entity_id: str
    label: str
    entity: str
    status: str
    progress: float
    stage: str
    attempt: int
    max_attempts: int
    available_at: datetime
    heartbeat_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    estimated_seconds: int | None
    retryable: bool
    error_code: str | None
    error_message: str | None
    error_details: dict[str, object] | None = Field(
        default=None,
        validation_alias="error_details_json",
    )

    @field_validator("error_details", mode="before")
    @classmethod
    def parse_error_details(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return {"raw": value}
            return decoded if isinstance(decoded, dict) else {"raw": decoded}
        return value

    @field_serializer(
        "available_at",
        "heartbeat_at",
        "created_at",
        "updated_at",
        "completed_at",
        when_used="json",
    )
    def serialize_job_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class JobRecoveryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: Literal[
        "RESUME_FROM_FAILURE",
        "RETRY_FAILED_PARTS",
        "SWITCH_MODEL",
        "FALLBACK_EXECUTION",
        "SAVE_INTERMEDIATE",
        "PROVIDE_INPUT",
    ]
    failed_part_ids: list[str] = Field(default_factory=list, max_length=100)
    model: str | None = Field(default=None, max_length=120)
    strategy: str | None = Field(default=None, max_length=120)
    additional_input: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "JobRecoveryRequest":
        if self.action == "PROVIDE_INPUT" and not self.additional_input:
            raise ValueError("补充信息不能为空")
        if self.action == "SWITCH_MODEL" and not (self.model or self.strategy):
            raise ValueError("切换模型或方案时至少需要提供一个目标")
        return self


class ProposalGenerateRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ProposalApprovalRequest(BaseModel):
    expected_version: int = Field(ge=1)
    assumptions_confirmed: bool
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class ProposalRead(BaseModel):
    id: str
    project_id: str
    version: int
    brief_version: int
    batch_id: str | None = None
    direction_key: str = "legacy"
    source_proposal_ids: list[str] = Field(default_factory=list)
    schema_version: str = "story-direction-v1"
    generation_evidence: dict[str, object] = Field(default_factory=dict)
    payload: dict[str, object]
    provider: str
    model: str
    config_version: str
    status: str
    approved_at: datetime | None
    approved_by: str | None
    created_at: datetime

    @field_serializer("approved_at", "created_at", when_used="json")
    def serialize_proposal_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class StoryDirectionMergeRequest(BaseModel):
    expected_version: int = Field(ge=1)
    source_proposal_ids: list[str] = Field(min_length=2, max_length=3)
    title: str | None = Field(default=None, min_length=1, max_length=160)


class StoryPackageGenerateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class ScriptApprovalRequest(BaseModel):
    expected_version: int = Field(ge=1)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class ScriptEpisodeUpdateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=160)


class ScriptSceneUpdateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    purpose: str | None = Field(default=None, min_length=1, max_length=2000)
    emotion: str | None = Field(default=None, min_length=1, max_length=80)
    bgm_intent: str | None = Field(default=None, max_length=1000)
    sfx_intents: list[str] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def require_scene_edit(self) -> "ScriptSceneUpdateRequest":
        if not self.model_dump(exclude={"expected_version"}, exclude_none=True):
            raise ValueError("至少提交一个场景修改字段")
        return self


class ScriptLineUpdateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    emotion: str | None = Field(default=None, min_length=1, max_length=80)
    speech_rate: float | None = Field(default=None, ge=0.7, le=1.4)
    pause_after_ms: int | None = Field(default=None, ge=0, le=3000)
    pronunciation: dict[str, str] | None = None
    localizations: dict[str, str] | None = None

    @model_validator(mode="after")
    def require_line_edit(self) -> "ScriptLineUpdateRequest":
        if not self.model_dump(exclude={"expected_version"}, exclude_none=True):
            raise ValueError("至少提交一个台词修改字段")
        return self


class ScriptExcerptRewriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    selection_start: int = Field(ge=0)
    selection_end: int = Field(ge=1)
    action: Literal[
        "REWRITE",
        "SHORTEN",
        "INTENSIFY_CONFLICT",
        "ADJUST_TONE",
        "CUSTOM",
    ]
    custom_instruction: str | None = Field(default=None, max_length=500)
    tone: str | None = Field(default=None, max_length=80)
    parent_revision_id: str | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def validate_rewrite_instruction(self) -> "ScriptExcerptRewriteRequest":
        if self.selection_end <= self.selection_start:
            raise ValueError("选区结束位置必须晚于开始位置")
        if self.action == "CUSTOM" and not self.custom_instruction:
            raise ValueError("自定义改写需要填写要求")
        if self.action == "ADJUST_TONE" and not self.tone:
            raise ValueError("调整语气需要选择目标语气")
        return self


class ScriptExcerptRewriteApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    script_id: str = Field(min_length=36, max_length=36)
    line_id: str = Field(min_length=36, max_length=36)


class GenericReviewDecisionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    decision: Literal["APPROVE", "REJECT"]
    issues: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=2000)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class RelationshipPerspectivePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    perceived_relationship: str = Field(min_length=1, max_length=1000)
    belief: str = Field(min_length=1, max_length=1000)


class RelationshipStatePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    surface_relationship: str = Field(min_length=1, max_length=1000)
    true_relationship: str = Field(min_length=1, max_length=1000)
    trust_level: int = Field(ge=-2, le=2)
    emotional_temperature: int = Field(ge=-2, le=2)
    power_balance: int = Field(ge=-2, le=2)
    conflict_intensity: int = Field(ge=0, le=4)


class FamilyKinshipPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    relation_type: Literal[
        "UNSPECIFIED",
        "BIOLOGICAL_PARENT_CHILD",
        "BIOLOGICAL_GRANDPARENT_GRANDCHILD",
        "FULL_SIBLINGS",
        "PATERNAL_HALF_SIBLINGS",
        "MATERNAL_HALF_SIBLINGS",
        "IDENTICAL_TWINS",
        "FRATERNAL_TWINS",
        "ADOPTIVE_PARENT_CHILD",
        "STEP_PARENT_CHILD",
        "IN_LAW",
        "OTHER_NON_BIOLOGICAL",
    ] = "UNSPECIFIED"
    shared_upbringing: Literal[
        "SAME_HOUSEHOLD",
        "PARTIAL",
        "SEPARATE",
        "UNKNOWN",
    ] = "UNKNOWN"
    upbringing_context: str | None = Field(default=None, max_length=1000)


class RelationshipUpbringingSuggestionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    family_kinship: FamilyKinshipPayload
    surface_relationship: str = Field(min_length=1, max_length=2000)
    true_relationship: str = Field(min_length=1, max_length=2000)


class RelationshipUpbringingSuggestionRead(BaseModel):
    suggestion: str = Field(min_length=20, max_length=1000)
    provider: str
    model: str
    warning: str | None = None


class RelationshipEdgePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    relationship_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    source_character_key: str = Field(min_length=1, max_length=80)
    target_character_key: str = Field(min_length=1, max_length=80)
    directionality: Literal["BIDIRECTIONAL", "DIRECTED"]
    relationship_types: list[
        Literal[
            "FAMILY",
            "ROMANTIC",
            "FRIENDSHIP",
            "ALLY",
            "RIVAL",
            "AUTHORITY",
            "DEPENDENCY",
            "DEBT",
            "CONTROL",
            "SECRET",
            "OTHER",
        ]
    ] = Field(min_length=1, max_length=8)
    family_kinship: FamilyKinshipPayload | None = None
    surface_relationship: str = Field(min_length=1, max_length=2000)
    true_relationship: str = Field(min_length=1, max_length=2000)
    source_view: RelationshipPerspectivePayload
    target_view: RelationshipPerspectivePayload
    trust_level: int = Field(ge=-2, le=2)
    emotional_temperature: int = Field(ge=-2, le=2)
    power_balance: int = Field(ge=-2, le=2)
    conflict_intensity: int = Field(ge=0, le=4)
    story_function: str = Field(min_length=1, max_length=2000)
    secret: str | None = Field(default=None, max_length=2000)
    is_core: bool = False
    locked: bool = False
    ordinal: int = Field(ge=1)

    @field_validator("relationship_types")
    @classmethod
    def require_unique_relationship_types(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("关系类型不能重复")
        return value

    @model_validator(mode="after")
    def reject_self_relationship(self) -> "RelationshipEdgePayload":
        if self.source_character_key == self.target_character_key:
            raise ValueError("不能创建角色与自身的关系")
        if self.family_kinship is not None and "FAMILY" not in self.relationship_types:
            raise ValueError("只有亲属关系可以填写血缘与共同成长信息")
        return self


class RelationshipBeatPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    relationship_key: str = Field(min_length=1, max_length=80)
    episode_ordinal: int = Field(ge=1)
    sequence: int = Field(ge=1)
    scene_ordinal: int | None = Field(default=None, ge=1)
    trigger_type: Literal[
        "STORY_EVENT",
        "MISJUDGMENT",
        "AUTHENTICATION",
        "REVEAL",
        "CHOICE",
        "BETRAYAL",
        "PAYOFF",
    ]
    trigger_ref: str | None = Field(default=None, max_length=120)
    before_state: RelationshipStatePayload
    after_state: RelationshipStatePayload
    evidence: str = Field(min_length=1, max_length=2000)
    emotional_consequence: str = Field(min_length=1, max_length=2000)
    audience_visibility: Literal["HIDDEN", "PARTIAL", "REVEALED"]
    ordinal: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_relationship_change(self) -> "RelationshipBeatPayload":
        if self.before_state == self.after_state:
            raise ValueError("关系变化前后状态不能完全相同")
        if self.trigger_type in {"MISJUDGMENT", "AUTHENTICATION"} and not self.trigger_ref:
            raise ValueError("误判或认证关系变化必须提供 trigger_ref")
        return self


class RelationshipGraphPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    schema_version: Literal["relationship-graph-v1"] = "relationship-graph-v1"
    edges: list[RelationshipEdgePayload] = Field(min_length=1, max_length=50)
    beats: list[RelationshipBeatPayload] = Field(default_factory=list, max_length=200)
    core_relationship_keys: list[str] = Field(default_factory=list, max_length=20)
    generation_notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_graph_structure(self) -> "RelationshipGraphPayload":
        relationship_keys = [item.relationship_key for item in self.edges]
        if len(relationship_keys) != len(set(relationship_keys)):
            raise ValueError("relationship_key 不能重复")

        character_pairs = [
            tuple(sorted((item.source_character_key, item.target_character_key)))
            for item in self.edges
        ]
        if len(character_pairs) != len(set(character_pairs)):
            raise ValueError("同一角色对只能保存一条规范关系")

        edge_ordinals = [item.ordinal for item in self.edges]
        if len(edge_ordinals) != len(set(edge_ordinals)):
            raise ValueError("关系 ordinal 不能重复")

        relationship_key_set = set(relationship_keys)
        if any(item.relationship_key not in relationship_key_set for item in self.beats):
            raise ValueError("关系变化引用了不存在的 relationship_key")

        core_keys = set(self.core_relationship_keys)
        if len(core_keys) != len(self.core_relationship_keys):
            raise ValueError("core_relationship_keys 不能重复")
        marked_core_keys = {item.relationship_key for item in self.edges if item.is_core}
        if core_keys != marked_core_keys:
            raise ValueError("core_relationship_keys 必须与 is_core 关系完全一致")

        grouped_beats: dict[tuple[str, int], list[RelationshipBeatPayload]] = {}
        for beat in self.beats:
            key = (beat.relationship_key, beat.episode_ordinal)
            grouped_beats.setdefault(key, []).append(beat)
        for beats in grouped_beats.values():
            ordered = sorted(beats, key=lambda item: item.sequence)
            sequences = [item.sequence for item in ordered]
            if sorted(sequences) != list(range(1, len(sequences) + 1)):
                raise ValueError("同一关系每集的 sequence 必须从 1 连续递增")
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if previous.after_state != current.before_state:
                    raise ValueError("相邻关系变化的前后状态必须连续")
        beat_ordinals = [item.ordinal for item in self.beats]
        if len(beat_ordinals) != len(set(beat_ordinals)):
            raise ValueError("同一关系每集的 beat ordinal 不能重复")
        return self


class RelationshipGraphValidationIssue(BaseModel):
    severity: Literal["BLOCKER", "WARNING", "INFO"]
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=2000)
    relationship_key: str | None = None
    character_key: str | None = None


class LegacyRelationshipSummary(BaseModel):
    raw_text: str
    status: Literal["MAPPED", "UNMAPPED"]
    source_character_key: str | None = None
    target_character_key: str | None = None
    relationship_key: str | None = None
    reason: str | None = None


class LegacyRelationshipAdapterResult(BaseModel):
    status: Literal["EMPTY", "MAPPED", "PARTIAL", "UNMAPPED"]
    summaries: list[LegacyRelationshipSummary]
    can_create_draft: bool


class RelationshipGraphEditability(BaseModel):
    semantic_editable: bool
    layout_editable: bool = True
    can_submit: bool
    can_approve: bool
    can_create_revision: bool
    active_job: bool
    reason_code: str | None = None
    reason_message: str | None = None
    requires_impact_confirmation: bool = False


class RelationshipGraphCreateRequest(BaseModel):
    expected_project_version: int = Field(ge=1)
    story_bible_version_id: str = Field(min_length=36, max_length=36)
    graph: RelationshipGraphPayload
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class RelationshipGraphUpdateRequest(BaseModel):
    expected_project_version: int = Field(ge=1)
    expected_graph_version: int = Field(ge=1)
    edges: list[RelationshipEdgePayload] = Field(min_length=1, max_length=50)
    beats: list[RelationshipBeatPayload] = Field(default_factory=list, max_length=200)
    core_relationship_keys: list[str] = Field(default_factory=list, max_length=20)
    generation_notes: list[str] = Field(default_factory=list, max_length=20)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)

    def graph_payload(self) -> RelationshipGraphPayload:
        return RelationshipGraphPayload(
            edges=self.edges,
            beats=self.beats,
            core_relationship_keys=self.core_relationship_keys,
            generation_notes=self.generation_notes,
        )


class RelationshipGraphActionRequest(BaseModel):
    expected_project_version: int = Field(ge=1)
    expected_graph_version: int = Field(ge=1)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=2000)


class RelationshipGraphRejectRequest(RelationshipGraphActionRequest):
    note: str = Field(min_length=1, max_length=2000)
    issues: list[str] = Field(default_factory=list, max_length=20)


class RelationshipGraphRevisionRequest(BaseModel):
    expected_project_version: int = Field(ge=1)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=2000)


class RelationshipRevisionImpactRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    base_relationship_graph_id: str = Field(min_length=36, max_length=36)
    relationship_keys: list[str] = Field(min_length=1, max_length=50)
    intent: str = Field(min_length=6, max_length=2000)
    expected_version: int = Field(ge=1)


class RelationshipRevisionCreateRequest(RelationshipRevisionImpactRequest):
    confirmed: bool
    impact_hash: str = Field(min_length=64, max_length=64)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class CharacterRevisionChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, min_length=1, max_length=240)
    gender: Literal["male", "female", "nonbinary", "unspecified"] | None = None
    ethnicity: str | None = Field(default=None, min_length=1, max_length=160)
    age: str | None = Field(default=None, min_length=1, max_length=80)
    height: str | None = Field(default=None, min_length=1, max_length=80)
    occupation: str | None = Field(default=None, min_length=1, max_length=160)
    personality: list[str] | None = Field(default=None, min_length=1, max_length=5)
    dramatic_function: str | None = Field(default=None, min_length=1, max_length=1000)
    desire: str | None = Field(default=None, min_length=1, max_length=1000)
    fear: str | None = Field(default=None, min_length=1, max_length=1000)
    secret: str | None = Field(default=None, max_length=1000)
    visual_notes: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_character_change(self) -> "CharacterRevisionChanges":
        if not self.model_dump(exclude_none=True):
            raise ValueError("至少修改一个角色字段")
        return self


class CharacterRevisionReviewRequest(BaseModel):
    base_story_bible_id: str = Field(min_length=36, max_length=36)
    base_relationship_graph_id: str = Field(min_length=36, max_length=36)
    character_key: str = Field(min_length=1, max_length=80)
    changes: CharacterRevisionChanges
    expected_version: int = Field(ge=1)


class CharacterRevisionCreateRequest(CharacterRevisionReviewRequest):
    confirmed: bool
    impact_hash: str = Field(min_length=64, max_length=64)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class StoryRead(BaseModel):
    id: str
    project_id: str
    version: int
    proposal_version: int
    title: str
    logline: str
    status: str
    content_hash: str
    approved_at: datetime
    approved_by: str

    @field_serializer("approved_at", when_used="json")
    def serialize_story_datetime(self, value: datetime) -> str:
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class CharacterLockRequest(BaseModel):
    expected_version: int = Field(ge=1)
    candidate_id: str = Field(min_length=36, max_length=36)


class CharacterVisualProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    identity_fields: dict[str, str] | None = None
    appearance_fields: dict[str, str] | None = None
    personality_visualization: dict[str, str] | None = None
    styling_fields: dict[str, str | list[str]] | None = None
    project_style: dict[str, str] | None = None
    negative_constraints: list[str] | None = Field(default=None, max_length=30)
    selected_direction: str | None = Field(default=None, max_length=120)
    actor: str = Field(default="创作者", min_length=1, max_length=80)

    @model_validator(mode="after")
    def require_visual_profile_change(self) -> "CharacterVisualProfileUpdateRequest":
        if not self.model_dump(exclude={"expected_version", "actor"}, exclude_none=True):
            raise ValueError("至少修改一个角色视觉字段")
        return self


class CharacterVisualProfileConfirmRequest(BaseModel):
    expected_version: int = Field(ge=1)
    profile_version_id: str = Field(min_length=36, max_length=36)
    actor: str = Field(default="创作者", min_length=1, max_length=80)


class CharacterCandidateGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    profile_version_id: str = Field(min_length=36, max_length=36)
    count: int = Field(default=3, ge=1, le=3)
    source_candidate_id: str | None = Field(default=None, min_length=36, max_length=36)
    refinement_note: str | None = Field(default=None, max_length=500)
    custom_prompt: str | None = Field(default=None, min_length=20, max_length=6000)
    actor: str = Field(default="创作者", min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_refinement(self) -> "CharacterCandidateGenerateRequest":
        instruction_count = int(bool(self.refinement_note)) + int(bool(self.custom_prompt))
        if bool(self.source_candidate_id) != bool(instruction_count):
            raise ValueError("基于候选重新生成必须同时提供来源候选和调整说明或自定义提示词")
        if instruction_count > 1:
            raise ValueError("调整说明和自定义提示词不能同时提交")
        return self


class CharacterCandidateSelectRequest(BaseModel):
    expected_version: int = Field(ge=1)
    candidate_id: str = Field(min_length=36, max_length=36)
    actor: str = Field(default="创作者", min_length=1, max_length=80)


class CharacterCandidateDeleteRequest(BaseModel):
    expected_version: int = Field(ge=1)
    actor: str = Field(default="创作者", min_length=1, max_length=80)


class CharacterIdentityLockRequest(BaseModel):
    expected_version: int = Field(ge=1)
    identity_version_id: str = Field(min_length=36, max_length=36)
    actor: str = Field(default="创作者", min_length=1, max_length=80)


class CharacterIdentityRestoreRequest(CharacterIdentityLockRequest):
    pass


class CharacterIdentityViewGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    view_type: Literal["FRONT", "THREE_QUARTER", "PROFILE", "FULL_BODY", "EXPRESSIONS"]
    refinement_note: str | None = Field(default=None, min_length=4, max_length=300)
    actor: str = Field(default="创作者", min_length=1, max_length=80)


class CharacterChangeApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    change_type: Literal["TEXT_ONLY", "STORY_STATE", "LOOK", "IDENTITY_MAJOR"]
    payload: dict[str, str | list[str]] = Field(default_factory=dict)
    decision: Literal["PRESERVE_IDENTITY", "REGENERATE"] | None = None
    actor: str = Field(default="创作者", min_length=1, max_length=80)


class CharacterCandidateRead(BaseModel):
    id: str
    character_id: str
    ordinal: int
    asset_id: str
    asset_url: str
    seed: str
    status: str
    selected: bool
    batch_id: str | None = None
    profile_version_id: str | None = None
    review_status: str = "PENDING_SELECTION"


class CharacterRead(BaseModel):
    id: str
    project_id: str
    character_key: str
    name: str
    role: str
    visual_brief: str
    status: str
    locked_candidate_id: str | None
    current_profile_version_id: str | None = None
    locked_identity_version_id: str | None = None
    active_look_version_id: str | None = None
    active_story_state_version_id: str | None = None
    lock_version: int
    candidates: list[CharacterCandidateRead]


class AssetRead(OrmModel):
    id: str
    project_id: str
    kind: str
    sha256: str
    mime: str
    size_bytes: int
    status: str
    provider: str
    is_temporary: bool
    width: int | None
    height: int | None
    duration_ms: int | None
    original_filename: str | None
    metadata: dict[str, object]
    rights_status: str
    source_entity_type: str
    source_entity_id: str
    created_at: datetime
    content_url: str

    @field_serializer("created_at", when_used="json")
    def serialize_asset_datetime(self, value: datetime) -> str:
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class TimelineRead(BaseModel):
    id: str
    project_id: str
    episode_id: str
    version: int
    status: str
    duration_ms: int
    baseline_hash: str
    approved_at: datetime | None
    assets: dict[str, str]

    @field_serializer("approved_at", when_used="json")
    def serialize_timeline_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class RevisionScope(BaseModel):
    type: Literal["SHOT", "SCENE", "PROJECT"]
    ids: list[str] = Field(min_length=1, max_length=10)


class RevisionImpactRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    scope: RevisionScope
    instruction: str = Field(min_length=6, max_length=2000)


class RevisionImpactRead(BaseModel):
    base_timeline_id: str
    scope: dict[str, object]
    intent: dict[str, object]
    affected: dict[str, object]
    estimated_points: int
    estimated_seconds: int
    requires_confirmation: bool
    story_dna_changed: bool
    touches_approved: bool


class RevisionCreateRequest(RevisionImpactRequest):
    confirmed: bool


class ChangeSetRead(BaseModel):
    id: str
    project_id: str
    base_timeline_id: str
    scope: dict[str, object]
    instruction: str
    impact: dict[str, object]
    estimate: dict[str, object]
    status: str
    result_timeline_id: str | None
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_change_datetime(self, value: datetime) -> str:
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class PreviewApprovalRequest(BaseModel):
    expected_version: int = Field(ge=1)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class PreviewRollbackRequest(PreviewApprovalRequest):
    pass


class PreviewCompareRead(BaseModel):
    left: TimelineRead
    right: TimelineRead
    changed_assets: list[str]
    unchanged_assets: list[str]
    changed_shot_ids: list[str]
    summary: str


class ExportEstimateRequest(BaseModel):
    profile: Literal["hybrid_720p"] = "hybrid_720p"


class ExportEstimateRead(BaseModel):
    timeline_id: str
    profile: str
    estimated_points: int
    estimated_seconds: int
    rights_status: str
    blocked: bool
    blockers: list[str]
    outputs: list[str]


class ExportCreateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    profile: Literal["hybrid_720p"] = "hybrid_720p"
    rights_confirmed: bool
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class ExportRead(BaseModel):
    id: str
    project_id: str
    timeline_id: str
    status: str
    profile: str
    export_profile_id: str | None = None
    language: str = "zh-CN"
    rights_status: str
    assets: dict[str, str]
    created_at: datetime
    completed_at: datetime | None

    @field_serializer("created_at", "completed_at", when_used="json")
    def serialize_export_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class EventRead(BaseModel):
    sequence: int
    event_id: str
    project_id: str
    job_id: str | None
    event_type: str
    payload: dict[str, object]
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_event_datetime(self, value: datetime) -> str:
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class WorkspaceRead(BaseModel):
    project: ProjectRead
    episode: EpisodeRead
    scenes: list[SceneRead]
    shots: list[ShotRead]
    jobs: list[JobRead]
