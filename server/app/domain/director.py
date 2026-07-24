from typing import Any, Literal

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
    actor: str = Field(default="demo-user", min_length=1, max_length=80)


class DirectorProposedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["SCENE", "LINE"]
    entity_id: str = Field(min_length=36, max_length=36)
    changes: dict[str, Any]
    before: dict[str, Any]


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
