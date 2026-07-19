from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.services.media import deterministic_png_bytes

MAX_IMAGE_BYTES = 32 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
ARK_IMAGE_SEED_MODULUS = 2**31


@dataclass(frozen=True)
class GeneratedImage:
    content: bytes
    mime: str
    width: int | None
    height: int | None
    model: str
    request_id: str | None
    source_url: str | None = None


class ImageProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _provider_message(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"][:500]
    if isinstance(payload.get("message"), str):
        return payload["message"][:500]
    return fallback


def _optional_positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def normalize_ark_image_seed(seed: int) -> int:
    """Map deterministic application seeds into Ark's signed int32 seed range."""
    return seed % ARK_IMAGE_SEED_MODULUS


async def generate_image(
    settings: Settings,
    prompt: str,
    *,
    model: str | None = None,
    size: str = "2K",
    reference_images: list[str] | None = None,
    seed: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeneratedImage:
    resolved_references = reference_images or []
    if not settings.ark_api_key:
        mock_key = f"{prompt}|seed={seed}|references={len(resolved_references)}"
        return GeneratedImage(
            content=deterministic_png_bytes(360, 640, mock_key),
            mime="image/png",
            width=360,
            height=640,
            model="deterministic-image-v1",
            request_id=None,
            source_url=None,
        )

    resolved_model = model or settings.ark_image_model
    request_payload: dict[str, object] = {
        "model": resolved_model,
        "prompt": prompt,
        "sequential_image_generation": "disabled",
        "response_format": "url",
        "size": size,
        "stream": False,
        "watermark": False,
    }
    if resolved_references:
        request_payload["image"] = resolved_references
    if seed is not None:
        request_payload["seed"] = normalize_ark_image_seed(seed)
    timeout = httpx.Timeout(settings.ark_request_timeout_seconds)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        transport=transport,
    ) as client:
        try:
            response = await client.post(
                settings.ark_images_url,
                headers={
                    "Authorization": f"Bearer {settings.ark_api_key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ImageProviderError(
                "ARK_NETWORK_ERROR",
                "连接火山方舟图片生成服务失败",
                retryable=True,
            ) from exc

        if response.is_error:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = None
            retryable = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
            code = "ARK_AUTH_ERROR" if response.status_code in {401, 403} else "ARK_API_ERROR"
            raise ImageProviderError(
                code,
                _provider_message(error_payload, f"火山方舟返回 HTTP {response.status_code}"),
                retryable=retryable,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                "ARK_INVALID_RESPONSE",
                "火山方舟返回了无法解析的响应",
                retryable=True,
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        item = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
        image_url = item.get("url") if item else None
        if not isinstance(image_url, str) or not image_url.startswith(("https://", "http://")):
            raise ImageProviderError(
                "ARK_IMAGE_URL_MISSING",
                "火山方舟响应中没有可下载的图片 URL",
                retryable=True,
            )

        try:
            image_response = await client.get(image_url)
            image_response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            raise ImageProviderError(
                "ARK_IMAGE_DOWNLOAD_FAILED",
                "Seedream 已生成图片，但下载结果失败",
                retryable=True,
            ) from exc

        content = image_response.content
        if not content or len(content) > MAX_IMAGE_BYTES:
            raise ImageProviderError(
                "ARK_IMAGE_INVALID_SIZE",
                "生成图片为空或超过 32 MB 限制",
                retryable=False,
            )
        mime = image_response.headers.get("content-type", "").split(";", 1)[0].lower()
        if mime not in ALLOWED_IMAGE_TYPES:
            raise ImageProviderError(
                "ARK_IMAGE_INVALID_TYPE",
                f"生成结果不是受支持的图片格式：{mime or 'unknown'}",
                retryable=False,
            )

        return GeneratedImage(
            content=content,
            mime=mime,
            width=_optional_positive_int(item.get("width")),
            height=_optional_positive_int(item.get("height")),
            model=str(payload.get("model") or resolved_model),
            request_id=response.headers.get("x-request-id"),
            source_url=image_url,
        )
