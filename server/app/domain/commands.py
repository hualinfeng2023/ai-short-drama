from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CommandActor(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["USER", "DIRECTOR", "SYSTEM"] = "USER"
    id: str = Field(min_length=1, max_length=80)


class ExpectedVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_lock_version: int | None = Field(default=None, ge=1)
    object_lock_version: int | None = Field(default=None, ge=1)
    target_version_id: str = Field(min_length=36, max_length=36)
    target_hash: str | None = Field(default=None, min_length=1, max_length=160)
    impact_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def require_lock_version(self) -> "ExpectedVersion":
        if self.project_lock_version is None and self.object_lock_version is None:
            raise ValueError("必须提供项目或目标对象的预期锁版本")
        return self


class DirectorCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    command_id: str = Field(min_length=36, max_length=36)
    command_type: Literal[
        "REVISE_SCRIPT",
        "UPDATE_CHARACTER_VISUAL_PROFILE",
        "CONFIRM_CHARACTER_VISUAL_PROFILE",
        "SET_SHOT_CHARACTER_BINDINGS",
        "APPROVE_SCRIPT",
        "APPROVE_STORYBOARD",
        "REVIEW_SHOT_TAKE",
        "CONFIRM_SHOT_TAKE_IDENTITY",
        "APPLY_SHOT_TAKE",
        "REQUEST_SHOT_IMAGE_GENERATION",
        "REQUEST_SHOT_VIDEO_GENERATION",
        "REQUEST_CHARACTER_CANDIDATE_GENERATION",
        "REQUEST_CHARACTER_IDENTITY_VIEW_GENERATION",
        "SELECT_CHARACTER_CANDIDATE",
        "DELETE_CHARACTER_CANDIDATE",
        "DELETE_REFERENCE_ASSET",
        "UPLOAD_REFERENCE_ASSET",
        "CANCEL_JOB",
        "RETRY_JOB",
        "RECOVER_JOB",
        "REQUEST_STORY_DIRECTION_GENERATION",
        "REQUEST_STORY_STRUCTURE_GENERATION",
        "MERGE_STORY_DIRECTIONS",
        "CREATE_CHARACTER_REVISION",
        "CREATE_SCRIPT_EXCERPT_REWRITE",
        "APPLY_SCRIPT_EXCERPT_REWRITE",
        "CREATE_RELATIONSHIP_GRAPH",
        "UPDATE_RELATIONSHIP_GRAPH",
        "SUBMIT_RELATIONSHIP_GRAPH",
        "WITHDRAW_RELATIONSHIP_GRAPH",
        "REJECT_RELATIONSHIP_GRAPH",
        "APPROVE_RELATIONSHIP_GRAPH",
        "CREATE_RELATIONSHIP_GRAPH_REVISION",
        "CREATE_CONFIRMED_RELATIONSHIP_REVISION",
        "SET_RELATIONSHIP_LOCK",
        "APPLY_CHARACTER_CHANGE",
        "REQUEST_STORYBOARD_SHOT_REGENERATION",
        "CREATE_REVISION_CHANGE_SET",
        "APPROVE_PREVIEW",
        "ROLLBACK_PREVIEW",
        "LOCK_CHARACTER_IDENTITY",
        "RESTORE_CHARACTER_IDENTITY",
        "CREATE_DIRECTOR_PROPOSAL",
        "APPLY_DIRECTOR_PROPOSAL",
        "DECIDE_DIRECTOR_PROPOSAL",
        "UPDATE_PROJECT_BRIEF",
        "REQUEST_DIRECTOR_PROPOSAL_GENERATION",
        "DECIDE_REVIEW",
        "CREATE_PROJECT",
        "DELETE_PROJECT",
        "APPROVE_PROPOSAL",
        "REQUEST_CHARACTER_CANDIDATES",
        "LOCK_CHARACTER_CANDIDATE",
        "APPROVE_PREPRODUCTION",
        "CREATE_EXPORT",
        "CREATE_EXPORT_PROFILE",
        "CREATE_EXPORT_MATRIX",
        "UPDATE_SHOT_SPEC",
        "REORDER_SCENE_SHOTS",
    ]
    actor: CommandActor
    target_object_id: str = Field(min_length=36, max_length=36)
    target_version_id: str = Field(min_length=36, max_length=36)
    expected_version: ExpectedVersion
    payload: dict[str, object]
    idempotency_key: str = Field(min_length=8, max_length=160)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
