from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import tos

from app.config import Settings
from app.db.models import Asset


class MediaStagingError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class StagedMedia:
    provider: str
    bucket: str
    object_key: str
    signed_url: str
    expires_at: datetime
    source_asset_id: str
    source_sha256: str
    upload_request_id: str | None

    def audit_metadata(self) -> dict[str, object]:
        """Return safe persisted metadata. The bearer-style signed URL is intentionally omitted."""
        return {
            "provider": self.provider,
            "bucket": self.bucket,
            "object_key": self.object_key,
            "expires_at": self.expires_at.isoformat(),
            "source_asset_id": self.source_asset_id,
            "source_sha256": self.source_sha256,
            "upload_request_id": self.upload_request_id,
        }


def media_staging_enabled(settings: Settings) -> bool:
    return bool(settings.feature_flags.get("provider_media_staging_v1", False))


def media_staging_configured(settings: Settings) -> bool:
    return bool(
        media_staging_enabled(settings)
        and settings.tos_access_key
        and settings.tos_secret_key
        and settings.tos_endpoint
        and settings.tos_region
        and settings.tos_bucket
    )


def seedream_fast_path_expires_at(
    settings: Settings,
    *,
    issued_at: datetime,
) -> datetime:
    normalized = (
        issued_at.replace(tzinfo=UTC) if issued_at.tzinfo is None else issued_at.astimezone(UTC)
    )
    return normalized + timedelta(seconds=settings.seedream_source_url_fast_path_seconds)


def seedream_fast_path_usable(
    source_url: object,
    expires_at: object,
    *,
    now: datetime | None = None,
) -> bool:
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        return False
    if not isinstance(expires_at, str):
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC) < parsed.astimezone(UTC)


def _client(settings: Settings) -> tos.TosClientV2:
    return tos.TosClientV2(
        settings.tos_access_key or "",
        settings.tos_secret_key or "",
        settings.tos_endpoint,
        settings.tos_region,
        security_token=settings.tos_security_token,
        request_timeout=min(60, max(10, int(settings.ark_request_timeout_seconds))),
        enable_crc=True,
        enable_verify_ssl=True,
    )


def _object_key(settings: Settings, asset: Asset, job_id: str, source: Path) -> str:
    suffix = (
        source.suffix.lower()
        if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        else ".img"
    )
    prefix = settings.tos_object_prefix or "ai-short-drama/media-staging"
    return f"{prefix}/{asset.project_id}/{job_id}/{asset.id}{suffix}"


def stage_asset_for_seedance(
    settings: Settings,
    *,
    asset: Asset,
    source: Path,
    job_id: str,
    client: Any | None = None,
    now: datetime | None = None,
) -> StagedMedia:
    if not media_staging_configured(settings):
        raise MediaStagingError(
            "TOS_MEDIA_STAGING_CONFIG_MISSING",
            "已启用媒体暂存，但 TOS AK、SK、Bucket、Region 或 Endpoint 配置不完整",
            retryable=False,
        )
    if not source.is_file():
        raise MediaStagingError(
            "TOS_MEDIA_SOURCE_MISSING",
            "待暂存的关键帧文件不存在",
            retryable=False,
        )

    resolved_client = client or _client(settings)
    bucket = settings.tos_bucket or ""
    object_key = _object_key(settings, asset, job_id, source)
    try:
        with source.open("rb") as body:
            uploaded = resolved_client.put_object(
                bucket,
                object_key,
                content=body,
                content_length=source.stat().st_size,
                content_type=asset.mime,
                acl=tos.ACLType.ACL_Private,
                meta={
                    "source-asset-id": asset.id,
                    "source-sha256": asset.sha256,
                },
                object_expires=settings.tos_object_expires_days,
            )
    except (tos.exceptions.TosClientError, tos.exceptions.TosServerError, OSError) as exc:
        status_code = getattr(exc, "status_code", None)
        retryable = status_code not in {400, 401, 403}
        raise MediaStagingError(
            "TOS_MEDIA_UPLOAD_FAILED",
            "关键帧上传到私有 TOS 失败",
            retryable=retryable,
        ) from exc

    try:
        signed = resolved_client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Get,
            bucket,
            object_key,
            expires=settings.tos_presign_ttl_seconds,
        )
        signed_url = str(signed.signed_url)
    except (tos.exceptions.TosClientError, tos.exceptions.TosServerError) as exc:
        try:
            resolved_client.delete_object(bucket, object_key)
        except (tos.exceptions.TosClientError, tos.exceptions.TosServerError):
            pass
        raise MediaStagingError(
            "TOS_MEDIA_PRESIGN_FAILED",
            "TOS 已接收关键帧，但生成短期下载地址失败",
            retryable=True,
        ) from exc
    if not signed_url.startswith("https://"):
        try:
            resolved_client.delete_object(bucket, object_key)
        except (tos.exceptions.TosClientError, tos.exceptions.TosServerError):
            pass
        raise MediaStagingError(
            "TOS_MEDIA_PRESIGN_NOT_HTTPS",
            "TOS 预签名结果不是 HTTPS 地址，请检查 Endpoint 配置",
            retryable=False,
        )

    issued_at = now or datetime.now(UTC)
    return StagedMedia(
        provider="volcengine-tos",
        bucket=bucket,
        object_key=object_key,
        signed_url=signed_url,
        expires_at=issued_at + timedelta(seconds=settings.tos_presign_ttl_seconds),
        source_asset_id=asset.id,
        source_sha256=asset.sha256,
        upload_request_id=getattr(uploaded, "request_id", None),
    )


def delete_staged_media(
    settings: Settings,
    staged: StagedMedia,
    *,
    client: Any | None = None,
) -> bool:
    if not settings.tos_cleanup_on_completion:
        return False
    resolved_client = client or _client(settings)
    try:
        resolved_client.delete_object(staged.bucket, staged.object_key)
    except (tos.exceptions.TosClientError, tos.exceptions.TosServerError):
        return False
    return True
