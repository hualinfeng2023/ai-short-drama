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
        "APPLY_SHOT_TAKE",
        "CREATE_REVISION_CHANGE_SET",
        "APPROVE_PREVIEW",
        "ROLLBACK_PREVIEW",
        "LOCK_CHARACTER_IDENTITY",
        "RESTORE_CHARACTER_IDENTITY",
    ]
    actor: CommandActor
    target_object_id: str = Field(min_length=36, max_length=36)
    target_version_id: str = Field(min_length=36, max_length=36)
    expected_version: ExpectedVersion
    payload: dict[str, object]
    idempotency_key: str = Field(min_length=8, max_length=160)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
