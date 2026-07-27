from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DirectorIssueType = Literal[
    "STORY_LOGIC",
    "CHARACTER_MOTIVATION",
    "AI_DIALOGUE",
    "PACING",
]


class DirectorProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    target_type: Literal["SCRIPT_SCENE", "SCENE"]
    target_id: str = Field(min_length=36, max_length=36)
    issue_types: list[DirectorIssueType] = Field(min_length=1, max_length=4)
    instruction: str | None = Field(default=None, max_length=1000)
    retry_of_generation_record_id: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
    )
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class DirectorLinePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str | None = Field(default=None, min_length=1, max_length=2000)
    emotion: str | None = Field(default=None, min_length=1, max_length=80)
    speech_rate: float | None = Field(default=None, ge=0.7, le=1.4)
    pause_after_ms: int | None = Field(default=None, ge=0, le=3000)

    @model_validator(mode="after")
    def require_patch(self) -> "DirectorLinePatch":
        values = self.model_dump(exclude_none=True)
        if not values:
            raise ValueError("LINE 修改至少需要一个允许字段")
        if len(values) != len(self.model_fields_set):
            raise ValueError("LINE 修改字段不能为 null")
        return self


class DirectorScenePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    purpose: str | None = Field(default=None, min_length=1, max_length=2000)
    emotion: str | None = Field(default=None, min_length=1, max_length=80)
    bgm_intent: str | None = Field(default=None, max_length=1000)
    sfx_intents: list[str] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def require_patch(self) -> "DirectorScenePatch":
        values = self.model_dump(exclude_none=True)
        if not values:
            raise ValueError("SCENE 修改至少需要一个允许字段")
        if len(values) != len(self.model_fields_set):
            raise ValueError("SCENE 修改字段不能为 null")
        return self


class DirectorLineProposedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["LINE"]
    entity_id: str = Field(min_length=36, max_length=36)
    changes: DirectorLinePatch
    before: DirectorLinePatch

    @model_validator(mode="after")
    def require_matching_before(self) -> "DirectorLineProposedChange":
        if self.changes.model_fields_set != self.before.model_fields_set:
            raise ValueError("LINE before 字段必须与 changes 完全对应")
        return self


class DirectorSceneProposedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["SCENE"]
    entity_id: str = Field(min_length=36, max_length=36)
    changes: DirectorScenePatch
    before: DirectorScenePatch

    @model_validator(mode="after")
    def require_matching_before(self) -> "DirectorSceneProposedChange":
        if self.changes.model_fields_set != self.before.model_fields_set:
            raise ValueError("SCENE before 字段必须与 changes 完全对应")
        return self


DirectorProposedChange = Annotated[
    DirectorLineProposedChange | DirectorSceneProposedChange,
    Field(discriminator="scope"),
]


class DirectorOption(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    option_id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=1000)
    proposed_change: DirectorProposedChange
    estimated_time_seconds: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)


class DirectorReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    issue_type: DirectorIssueType
    observation: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    options: list[DirectorOption] = Field(min_length=2, max_length=3)
    recommended_option_id: str = Field(min_length=1, max_length=40)
    confidence: float = Field(ge=0, le=1)
    validation_plan: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_recommendation(self) -> "DirectorReviewOutput":
        option_ids = [item.option_id for item in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("Director 方案 ID 不能重复")
        if self.recommended_option_id not in option_ids:
            raise ValueError("推荐方案必须来自 options")
        return self


def director_change_target_issues(
    review: DirectorReviewOutput,
    *,
    scene_id: str,
    line_ids: set[str],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for option in review.options:
        change = option.proposed_change
        if change.scope == "SCENE" and change.entity_id != scene_id:
            issues.append(
                {
                    "code": "SCENE_TARGET_INVALID",
                    "option_id": option.option_id,
                    "scope": change.scope,
                    "entity_id": change.entity_id,
                }
            )
        elif change.scope == "LINE" and change.entity_id not in line_ids:
            issues.append(
                {
                    "code": "LINE_TARGET_INVALID",
                    "option_id": option.option_id,
                    "scope": change.scope,
                    "entity_id": change.entity_id,
                }
            )
    return issues


class DirectorProposalExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    option_id: str = Field(min_length=1, max_length=40)
    actor: str = Field(default="demo-user", min_length=1, max_length=80)
    confirmed: bool


class DirectorProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    decision: Literal["APPROVE", "REJECT", "ROLLBACK"]
    actor: str = Field(default="demo-user", min_length=1, max_length=80)
    confirmed: bool
    override_reason: str | None = Field(default=None, min_length=8, max_length=1000)
