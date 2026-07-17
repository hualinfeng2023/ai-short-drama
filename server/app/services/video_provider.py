import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

import httpx

from app.config import Settings

MAX_VIDEO_BYTES = 256 * 1024 * 1024
TaskCreatedCallback = Callable[[str], Awaitable[None]]
PollCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class GeneratedVideo:
    content: bytes
    mime: str
    duration_ms: int | None
    model: str
    request_id: str | None
    provider_task_id: str
    source_url: str


class VideoProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _message(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"][:500]
    if isinstance(payload.get("message"), str):
        return payload["message"][:500]
    return fallback


def _error_code(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"][:80]
    return fallback


def _duration_ms(value: object) -> int | None:
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(seconds * 1000) if seconds > 0 else None


async def _json_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, object] | None = None,
) -> tuple[dict[str, Any], str | None]:
    try:
        response = await client.request(method, url, headers=headers, json=json_body)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise VideoProviderError(
            "ARK_VIDEO_NETWORK_ERROR",
            "连接火山方舟视频生成服务失败",
            retryable=True,
        ) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.is_error:
        retryable = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
        code = (
            "ARK_VIDEO_AUTH_ERROR" if response.status_code in {401, 403} else "ARK_VIDEO_API_ERROR"
        )
        raise VideoProviderError(
            code,
            _message(payload, f"火山方舟视频服务返回 HTTP {response.status_code}"),
            retryable=retryable,
        )
    if not isinstance(payload, dict):
        raise VideoProviderError(
            "ARK_VIDEO_INVALID_RESPONSE",
            "火山方舟视频服务返回了无法解析的响应",
            retryable=True,
        )
    return payload, response.headers.get("x-request-id")


async def generate_video(
    settings: Settings,
    *,
    prompt: str,
    image_url: str,
    provider_task_id: str | None = None,
    on_task_created: TaskCreatedCallback | None = None,
    on_poll: PollCallback | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeneratedVideo:
    if not settings.ark_api_key:
        raise VideoProviderError(
            "ARK_API_KEY_MISSING",
            "服务端尚未配置 ARK_API_KEY",
            retryable=False,
        )
    headers = {
        "Authorization": f"Bearer {settings.ark_api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings.ark_request_timeout_seconds)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        transport=transport,
    ) as client:
        request_id: str | None = None
        if provider_task_id is None:
            created, request_id = await _json_request(
                client,
                "POST",
                settings.ark_video_tasks_url,
                headers=headers,
                json_body={
                    "model": settings.ark_video_model,
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            )
            task_id = created.get("id")
            if not isinstance(task_id, str) or not task_id:
                raise VideoProviderError(
                    "ARK_VIDEO_TASK_ID_MISSING",
                    "火山方舟响应中没有视频任务 ID",
                    retryable=True,
                )
            provider_task_id = task_id
            if on_task_created is not None:
                await on_task_created(provider_task_id)

        started = monotonic()
        result: dict[str, Any]
        while True:
            result, poll_request_id = await _json_request(
                client,
                "GET",
                f"{settings.ark_video_tasks_url}/{provider_task_id}",
                headers=headers,
            )
            request_id = poll_request_id or request_id
            status = result.get("status")
            if status == "succeeded":
                break
            if status == "failed":
                provider_code = _error_code(result, "ARK_VIDEO_TASK_FAILED")
                retryable = provider_code in {"InternalServiceError", "ServiceUnavailable"}
                raise VideoProviderError(
                    provider_code,
                    _message(result, "Seedance 视频生成任务失败"),
                    retryable=retryable,
                )
            if status == "cancelled":
                raise VideoProviderError(
                    "ARK_VIDEO_TASK_CANCELLED",
                    "Seedance 视频生成任务已取消",
                    retryable=False,
                )
            if status not in {"queued", "running"}:
                raise VideoProviderError(
                    "ARK_VIDEO_UNKNOWN_STATUS",
                    f"Seedance 返回未知任务状态：{status}",
                    retryable=True,
                )
            if on_poll is not None:
                await on_poll(str(status))
            if monotonic() - started >= settings.ark_video_timeout_seconds:
                raise VideoProviderError(
                    "ARK_VIDEO_TIMEOUT",
                    "等待 Seedance 视频生成超时，可稍后继续查询同一任务",
                    retryable=True,
                )
            await asyncio.sleep(settings.ark_video_poll_interval_seconds)

        content = result.get("content")
        video_url = content.get("video_url") if isinstance(content, dict) else None
        if not isinstance(video_url, str) or not video_url.startswith(("https://", "http://")):
            raise VideoProviderError(
                "ARK_VIDEO_URL_MISSING",
                "Seedance 成功响应中没有视频 URL",
                retryable=True,
            )
        try:
            video_response = await client.get(video_url)
            video_response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            raise VideoProviderError(
                "ARK_VIDEO_DOWNLOAD_FAILED",
                "Seedance 已生成视频，但下载结果失败",
                retryable=True,
            ) from exc
        video_bytes = video_response.content
        if not video_bytes or len(video_bytes) > MAX_VIDEO_BYTES:
            raise VideoProviderError(
                "ARK_VIDEO_INVALID_SIZE",
                "生成视频为空或超过 256 MB 限制",
                retryable=False,
            )
        mime = video_response.headers.get("content-type", "").split(";", 1)[0].lower()
        if mime not in {"video/mp4", "application/octet-stream"}:
            raise VideoProviderError(
                "ARK_VIDEO_INVALID_TYPE",
                f"生成结果不是 MP4 视频：{mime or 'unknown'}",
                retryable=False,
            )
        return GeneratedVideo(
            content=video_bytes,
            mime="video/mp4",
            duration_ms=_duration_ms(result.get("duration")),
            model=str(result.get("model") or settings.ark_video_model),
            request_id=request_id,
            provider_task_id=provider_task_id,
            source_url=video_url,
        )


async def cancel_video_task(
    settings: Settings,
    provider_task_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    if not settings.ark_api_key:
        return
    headers = {"Authorization": f"Bearer {settings.ark_api_key}"}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.ark_request_timeout_seconds),
        transport=transport,
    ) as client:
        try:
            response = await client.delete(
                f"{settings.ark_video_tasks_url}/{provider_task_id}",
                headers=headers,
            )
            if response.status_code not in {200, 204, 404, 409}:
                response.raise_for_status()
        except httpx.HTTPError:
            return
