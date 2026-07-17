import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.services.image_provider import GeneratedImage


@dataclass(frozen=True)
class IdentityEvaluation:
    status: str
    score: float | None
    message: str
    provider: str
    model: str | None


def image_data_url(content: bytes, mime: str) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _output_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return None


def _parse_result(text: str) -> tuple[bool, float, str]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("identity response is not JSON")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("identity response is not an object")
    same_identity = payload.get("same_identity")
    confidence = payload.get("confidence")
    reason = payload.get("reason")
    if not isinstance(same_identity, bool):
        raise ValueError("same_identity is missing")
    if not isinstance(confidence, int | float):
        raise ValueError("confidence is missing")
    score = min(1.0, max(0.0, float(confidence)))
    message = str(reason).strip()[:300] if reason else "视觉身份评分已完成"
    return same_identity, score, message


async def evaluate_identity_consistency(
    settings: Settings,
    *,
    reference_images: list[str],
    generated_image: GeneratedImage,
    character_labels: list[str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> IdentityEvaluation:
    if not reference_images:
        return IdentityEvaluation(
            status="NOT_APPLICABLE",
            score=None,
            message="该分镜未绑定出场角色，无需执行角色身份检查",
            provider="rules",
            model=None,
        )
    if not settings.ark_api_key or not settings.ark_identity_qc_enabled:
        return IdentityEvaluation(
            status="REVIEW_REQUIRED",
            score=None,
            message="角色参考图已注入生成请求；自动视觉评分未启用，请人工确认人物身份",
            provider="manual-gate",
            model=None,
        )

    labels = "、".join(character_labels) or "绑定角色"
    instruction = (
        "你是影视角色身份一致性审核员。前面的图片依次是锁定的角色参考图，"
        "最后一张图片是新生成的分镜。只比较人物身份特征（脸型、五官比例、发型核心特征），"
        "不要因景别、表情、姿势、光线、服装细节或背景变化而误判。"
        f"本镜头绑定角色：{labels}。若任一应出现角色明显不是同一人物，same_identity 必须为 false。"
        "只输出一个 JSON 对象，不要 Markdown："
        '{"same_identity":true,"confidence":0.0,"reason":"不超过80字的中文理由"}'
    )
    content: list[dict[str, str]] = [{"type": "input_text", "text": instruction}]
    content.extend({"type": "input_image", "image_url": image} for image in reference_images)
    content.append(
        {
            "type": "input_image",
            "image_url": image_data_url(generated_image.content, generated_image.mime),
        }
    )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ark_request_timeout_seconds),
            transport=transport,
        ) as client:
            response = await client.post(
                settings.ark_responses_url,
                headers={
                    "Authorization": f"Bearer {settings.ark_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.ark_prompt_model,
                    "input": [{"type": "message", "role": "user", "content": content}],
                    "thinking": {"type": "disabled"},
                },
            )
            response.raise_for_status()
            output = _output_text(response.json())
            if output is None:
                raise ValueError("identity response text missing")
            same_identity, score, message = _parse_result(output)
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        return IdentityEvaluation(
            status="REVIEW_REQUIRED",
            score=None,
            message="自动视觉身份检查暂时不可用，已安全降级为人工确认",
            provider="manual-gate",
            model=settings.ark_prompt_model,
        )

    passed = same_identity and score >= settings.ark_identity_auto_pass_threshold
    return IdentityEvaluation(
        status="PASSED" if passed else "REVIEW_REQUIRED",
        score=score,
        message=(
            message
            if passed
            else f"自动检查未达到 {settings.ark_identity_auto_pass_threshold:.0%} 门槛：{message}"
        ),
        provider="volcengine-ark",
        model=settings.ark_prompt_model,
    )
