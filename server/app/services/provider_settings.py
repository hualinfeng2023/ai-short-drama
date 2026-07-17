import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import httpx
import tos

if TYPE_CHECKING:
    from app.config import Settings

PROVIDER_SETTINGS_FILENAME = "provider_settings.json"
SECRET_SETTING_KEYS = {
    "ARK_API_KEY",
    "TOS_ACCESS_KEY",
    "TOS_SECRET_KEY",
    "TOS_SECURITY_TOKEN",
}
EDITABLE_PROVIDER_SETTING_KEYS = SECRET_SETTING_KEYS | {
    "ARK_IMAGES_URL",
    "ARK_IMAGE_MODEL",
    "ARK_REQUEST_TIMEOUT_SECONDS",
    "ARK_RESPONSES_URL",
    "ARK_PROMPT_MODEL",
    "ARK_IDENTITY_QC_ENABLED",
    "ARK_IDENTITY_AUTO_PASS_THRESHOLD",
    "ARK_VIDEO_TASKS_URL",
    "ARK_VIDEO_MODEL",
    "ARK_VIDEO_POLL_INTERVAL_SECONDS",
    "ARK_VIDEO_TIMEOUT_SECONDS",
    "SEEDREAM_SOURCE_URL_FAST_PATH_SECONDS",
    "TOS_ENDPOINT",
    "TOS_REGION",
    "TOS_BUCKET",
    "TOS_PRESIGN_TTL_SECONDS",
    "TOS_OBJECT_PREFIX",
    "TOS_OBJECT_EXPIRES_DAYS",
    "TOS_CLEANUP_ON_COMPLETION",
    "PROVIDER_MEDIA_STAGING_V1",
}


def provider_settings_path(data_dir: Path) -> Path:
    return data_dir / PROVIDER_SETTINGS_FILENAME


def load_provider_overrides(data_dir: Path) -> dict[str, object]:
    path = provider_settings_path(data_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    values = payload.get("values", payload)
    if not isinstance(values, dict):
        return {}
    return {
        key: value
        for key, value in values.items()
        if key in EDITABLE_PROVIDER_SETTING_KEYS and isinstance(value, (str, int, float, bool))
    }


def write_provider_overrides(data_dir: Path, values: dict[str, object]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = provider_settings_path(data_dir)
    temporary_path = path.with_suffix(".tmp")
    filtered = {
        key: value
        for key, value in values.items()
        if key in EDITABLE_PROVIDER_SETTING_KEYS and isinstance(value, (str, int, float, bool))
    }
    payload = {
        "version": 1,
        "updated_at": datetime.now(UTC).isoformat(),
        "values": filtered,
    }
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, path)
    os.chmod(path, 0o600)


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    suffix = value[-4:] if len(value) >= 4 else value
    return f"••••{suffix}"


def setting_source(data_dir: Path, setting_name: str) -> str:
    if setting_name in load_provider_overrides(data_dir):
        return "saved"
    if os.getenv(setting_name):
        return "environment"
    return "default"


def provider_settings_snapshot(settings: "Settings") -> dict[str, object]:
    path = provider_settings_path(settings.data_dir)
    updated_at = (
        datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat() if path.is_file() else None
    )
    return {
        "storage": {
            "scope": "server_data",
            "updated_at": updated_at,
            "secrets_returned": False,
        },
        "ark": {
            "api_key_configured": bool(settings.ark_api_key),
            "api_key_hint": mask_secret(settings.ark_api_key),
            "api_key_source": setting_source(settings.data_dir, "ARK_API_KEY"),
            "responses_url": settings.ark_responses_url,
            "prompt_model": settings.ark_prompt_model,
            "images_url": settings.ark_images_url,
            "image_model": settings.ark_image_model,
            "video_tasks_url": settings.ark_video_tasks_url,
            "video_model": settings.ark_video_model,
            "request_timeout_seconds": settings.ark_request_timeout_seconds,
            "video_poll_interval_seconds": settings.ark_video_poll_interval_seconds,
            "video_timeout_seconds": settings.ark_video_timeout_seconds,
            "source_url_fast_path_seconds": settings.seedream_source_url_fast_path_seconds,
            "identity_qc_enabled": settings.ark_identity_qc_enabled,
            "identity_auto_pass_threshold": settings.ark_identity_auto_pass_threshold,
        },
        "tos": {
            "enabled": bool(settings.feature_flags.get("provider_media_staging_v1", False)),
            "access_key_configured": bool(settings.tos_access_key),
            "access_key_hint": mask_secret(settings.tos_access_key),
            "access_key_source": setting_source(settings.data_dir, "TOS_ACCESS_KEY"),
            "secret_key_configured": bool(settings.tos_secret_key),
            "secret_key_hint": mask_secret(settings.tos_secret_key),
            "security_token_configured": bool(settings.tos_security_token),
            "endpoint": settings.tos_endpoint,
            "region": settings.tos_region,
            "bucket": settings.tos_bucket or "",
            "presign_ttl_seconds": settings.tos_presign_ttl_seconds,
            "object_prefix": settings.tos_object_prefix,
            "object_expires_days": settings.tos_object_expires_days,
            "cleanup_on_completion": settings.tos_cleanup_on_completion,
        },
    }


def _models_url(responses_url: str) -> str:
    parsed = urlsplit(responses_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/responses"):
        path = f"{path[: -len('/responses')]}/models"
    else:
        path = f"{path.rsplit('/', 1)[0]}/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


async def test_ark_provider(settings: "Settings") -> dict[str, object]:
    if not settings.ark_api_key:
        return {
            "provider": "volcengine-ark",
            "status": "not_configured",
            "message": "请先保存方舟 API Key",
        }
    models_url = _models_url(settings.ark_responses_url)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(min(settings.ark_request_timeout_seconds, 20)),
        ) as client:
            response = await client.get(
                models_url,
                headers={"Authorization": f"Bearer {settings.ark_api_key}"},
            )
    except httpx.HTTPError:
        return {
            "provider": "volcengine-ark",
            "status": "error",
            "message": "无法连接方舟服务，请检查网络和接口地址",
        }
    if response.status_code in {401, 403}:
        return {
            "provider": "volcengine-ark",
            "status": "error",
            "message": "方舟鉴权失败，请检查 API Key",
        }
    if response.status_code >= 400:
        return {
            "provider": "volcengine-ark",
            "status": "error",
            "message": f"方舟连接测试失败（HTTP {response.status_code}）",
        }
    return {
        "provider": "volcengine-ark",
        "status": "connected",
        "message": "方舟 API 连接成功",
        "endpoint": models_url,
    }


async def test_tos_provider(settings: "Settings") -> dict[str, object]:
    if not all(
        (
            settings.tos_access_key,
            settings.tos_secret_key,
            settings.tos_endpoint,
            settings.tos_region,
            settings.tos_bucket,
        )
    ):
        return {
            "provider": "volcengine-tos",
            "status": "not_configured",
            "message": "请先补全 TOS AK、SK、Endpoint、Region 和 Bucket",
        }

    def head_bucket() -> None:
        client = tos.TosClientV2(
            settings.tos_access_key or "",
            settings.tos_secret_key or "",
            settings.tos_endpoint,
            settings.tos_region,
            security_token=settings.tos_security_token,
            request_timeout=20,
            socket_timeout=20,
        )
        client.head_bucket(settings.tos_bucket or "")

    try:
        await asyncio.to_thread(head_bucket)
    except tos.exceptions.TosServerError as exc:
        status_code = getattr(exc, "status_code", None)
        return {
            "provider": "volcengine-tos",
            "status": "error",
            "message": f"TOS 连接测试失败（HTTP {status_code or 'unknown'}）",
        }
    except (tos.exceptions.TosClientError, OSError):
        return {
            "provider": "volcengine-tos",
            "status": "error",
            "message": "无法连接 TOS，请检查网络、Endpoint 和凭证",
        }
    return {
        "provider": "volcengine-tos",
        "status": "connected",
        "message": "TOS Bucket 连接成功",
        "bucket": settings.tos_bucket,
    }
