from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.trace import success
from app.config import get_settings
from app.services.media_staging import media_staging_configured
from app.services.provider_settings import (
    load_provider_overrides,
    provider_settings_snapshot,
    test_ark_provider,
    test_tos_provider,
    write_provider_overrides,
)

router = APIRouter(prefix="/api/v1/settings/providers", tags=["provider-settings"])


def _validate_http_url(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("接口地址必须是有效的 HTTP 或 HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("接口地址不能包含用户名或密码")
    return candidate


class ArkSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_api_key: bool = False
    responses_url: str = Field(min_length=8, max_length=500)
    prompt_model: str = Field(min_length=1, max_length=160)
    images_url: str = Field(min_length=8, max_length=500)
    image_model: str = Field(min_length=1, max_length=160)
    video_tasks_url: str = Field(min_length=8, max_length=500)
    video_model: str = Field(min_length=1, max_length=160)
    request_timeout_seconds: float = Field(ge=5, le=900)
    video_poll_interval_seconds: float = Field(ge=1, le=60)
    video_timeout_seconds: float = Field(ge=30, le=3600)
    source_url_fast_path_seconds: int = Field(ge=60, le=3600)
    identity_qc_enabled: bool
    identity_auto_pass_threshold: float = Field(ge=0.5, le=1)

    @field_validator("responses_url", "images_url", "video_tasks_url")
    @classmethod
    def validate_urls(cls, value: str) -> str:
        return _validate_http_url(value)

    @model_validator(mode="after")
    def validate_secret_action(self) -> "ArkSettingsUpdate":
        if self.api_key is not None and self.clear_api_key:
            raise ValueError("不能同时设置和清除方舟 API Key")
        return self


class TosSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool
    access_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_access_key: bool = False
    secret_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_secret_key: bool = False
    security_token: str | None = Field(default=None, min_length=1, max_length=8192)
    clear_security_token: bool = False
    endpoint: str = Field(min_length=3, max_length=255)
    region: str = Field(min_length=2, max_length=80)
    bucket: str = Field(default="", max_length=255)
    presign_ttl_seconds: int = Field(ge=900, le=86400)
    object_prefix: str = Field(min_length=1, max_length=500)
    object_expires_days: int = Field(ge=1, le=7)
    cleanup_on_completion: bool

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        candidate = value.strip()
        if "://" in candidate or any(character.isspace() for character in candidate):
            raise ValueError("TOS Endpoint 只填写域名，不包含协议或空格")
        return candidate

    @field_validator("object_prefix")
    @classmethod
    def normalize_prefix(cls, value: str) -> str:
        return value.strip("/")

    @model_validator(mode="after")
    def validate_secret_actions(self) -> "TosSettingsUpdate":
        pairs = (
            (self.access_key, self.clear_access_key, "TOS Access Key"),
            (self.secret_key, self.clear_secret_key, "TOS Secret Key"),
            (self.security_token, self.clear_security_token, "TOS Security Token"),
        )
        for value, clear, label in pairs:
            if value is not None and clear:
                raise ValueError(f"不能同时设置和清除 {label}")
        return self


class ProviderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ark: ArkSettingsUpdate
    tos: TosSettingsUpdate


class ProviderConnectionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["ark", "tos"]


def _apply_secret(
    values: dict[str, object],
    *,
    key: str,
    replacement: str | None,
    clear: bool,
) -> None:
    if replacement is not None:
        values[key] = replacement
    elif clear:
        values[key] = ""


@router.get("")
def read_provider_settings() -> dict[str, object]:
    return success(provider_settings_snapshot(get_settings()))


@router.patch("")
def update_provider_settings(
    payload: ProviderSettingsUpdate,
    request: Request,
) -> dict[str, object]:
    current_settings = get_settings()
    previous = load_provider_overrides(current_settings.data_dir)
    values = dict(previous)
    values.update(
        {
            "ARK_RESPONSES_URL": payload.ark.responses_url,
            "ARK_PROMPT_MODEL": payload.ark.prompt_model,
            "ARK_IMAGES_URL": payload.ark.images_url,
            "ARK_IMAGE_MODEL": payload.ark.image_model,
            "ARK_VIDEO_TASKS_URL": payload.ark.video_tasks_url,
            "ARK_VIDEO_MODEL": payload.ark.video_model,
            "ARK_REQUEST_TIMEOUT_SECONDS": payload.ark.request_timeout_seconds,
            "ARK_VIDEO_POLL_INTERVAL_SECONDS": payload.ark.video_poll_interval_seconds,
            "ARK_VIDEO_TIMEOUT_SECONDS": payload.ark.video_timeout_seconds,
            "SEEDREAM_SOURCE_URL_FAST_PATH_SECONDS": payload.ark.source_url_fast_path_seconds,
            "ARK_IDENTITY_QC_ENABLED": payload.ark.identity_qc_enabled,
            "ARK_IDENTITY_AUTO_PASS_THRESHOLD": payload.ark.identity_auto_pass_threshold,
            "PROVIDER_MEDIA_STAGING_V1": payload.tos.enabled,
            "TOS_ENDPOINT": payload.tos.endpoint,
            "TOS_REGION": payload.tos.region,
            "TOS_BUCKET": payload.tos.bucket,
            "TOS_PRESIGN_TTL_SECONDS": payload.tos.presign_ttl_seconds,
            "TOS_OBJECT_PREFIX": payload.tos.object_prefix,
            "TOS_OBJECT_EXPIRES_DAYS": payload.tos.object_expires_days,
            "TOS_CLEANUP_ON_COMPLETION": payload.tos.cleanup_on_completion,
        }
    )
    _apply_secret(
        values,
        key="ARK_API_KEY",
        replacement=payload.ark.api_key,
        clear=payload.ark.clear_api_key,
    )
    _apply_secret(
        values,
        key="TOS_ACCESS_KEY",
        replacement=payload.tos.access_key,
        clear=payload.tos.clear_access_key,
    )
    _apply_secret(
        values,
        key="TOS_SECRET_KEY",
        replacement=payload.tos.secret_key,
        clear=payload.tos.clear_secret_key,
    )
    _apply_secret(
        values,
        key="TOS_SECURITY_TOKEN",
        replacement=payload.tos.security_token,
        clear=payload.tos.clear_security_token,
    )
    write_provider_overrides(current_settings.data_dir, values)
    resolved = get_settings()
    if payload.tos.enabled and not media_staging_configured(resolved):
        write_provider_overrides(current_settings.data_dir, previous)
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TOS_CONFIGURATION_INCOMPLETE",
                "message": "启用 TOS 中转前必须补全 AK、SK、Endpoint、Region 和 Bucket",
            },
        )
    worker = getattr(request.app.state, "job_worker", None)
    if worker is not None:
        worker.settings = resolved
    return success(provider_settings_snapshot(resolved))


@router.post("/test")
async def test_provider_connection(payload: ProviderConnectionTest) -> dict[str, object]:
    settings = get_settings()
    result = (
        await test_ark_provider(settings)
        if payload.provider == "ark"
        else await test_tos_provider(settings)
    )
    return success(result)
