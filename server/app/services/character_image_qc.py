import json
from dataclasses import dataclass
from io import BytesIO

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import Settings
from app.services.identity_consistency import _output_text, image_data_url
from app.services.image_provider import GeneratedImage

CHARACTER_IMAGE_CHECK_TYPES = (
    "WATERMARK_FREE",
    "FOREGROUND_CLEAR",
    "BODY_CONTINUITY",
    "PURE_WHITE_BACKGROUND",
)


@dataclass(frozen=True)
class CharacterImageCheck:
    check_type: str
    status: str
    score: float | None
    message: str


@dataclass(frozen=True)
class CharacterImageQualityReport:
    status: str
    provider: str
    model: str | None
    checks: tuple[CharacterImageCheck, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "checks": [
                {
                    "type": item.check_type,
                    "status": item.status,
                    "score": item.score,
                    "message": item.message,
                }
                for item in self.checks
            ],
        }


def _open_rgb(content: bytes) -> Image.Image | None:
    try:
        with Image.open(BytesIO(content)) as source:
            return ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, UnidentifiedImageError):
        return None


def detect_lower_right_watermark(content: bytes, mime: str) -> bool:
    """Detect a compact, high-contrast label in the usual lower-right watermark area."""
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        return False
    image = _open_rgb(content)
    if image is None or image.width < 64 or image.height < 64:
        return False

    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    left = round(image.width * 0.76)
    top = round(image.height * 0.84)
    region = ImageOps.grayscale(image.crop((left, top, image.width, image.height)))
    if region.width < 8 or region.height < 8:
        return False

    pixels = list(region.get_flattened_data())
    background = sorted(pixels)[len(pixels) // 2]
    contrasting = [abs(value - background) >= 55 for value in pixels]
    contrast_ratio = sum(contrasting) / len(contrasting)
    if not 0.006 <= contrast_ratio <= 0.46:
        return False

    xs: list[int] = []
    ys: list[int] = []
    for index, differs in enumerate(contrasting):
        if differs:
            xs.append(index % region.width)
            ys.append(index // region.width)
    if not xs:
        return False
    box_width = max(xs) - min(xs) + 1
    box_height = max(ys) - min(ys) + 1
    return (
        box_width / max(box_height, 1) >= 1.35
        and box_height <= region.height * 0.72
        and max(ys) >= region.height * 0.45
    )


def _white_background_ratio(content: bytes) -> float | None:
    image = _open_rgb(content)
    if image is None:
        return None
    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    border_width = max(2, round(min(image.size) * 0.04))
    samples: list[tuple[int, int, int]] = []
    samples.extend(image.crop((0, 0, image.width, border_width)).get_flattened_data())
    samples.extend(
        image.crop(
            (0, image.height - border_width, image.width, image.height)
        ).get_flattened_data()
    )
    samples.extend(
        image.crop(
            (0, border_width, border_width, image.height - border_width)
        ).get_flattened_data()
    )
    samples.extend(
        image.crop(
            (image.width - border_width, border_width, image.width, image.height - border_width)
        ).get_flattened_data()
    )
    if not samples:
        return None
    white = sum(1 for red, green, blue in samples if min(red, green, blue) >= 248)
    return white / len(samples)


def _manual_report(image: GeneratedImage) -> CharacterImageQualityReport:
    watermark_free = not detect_lower_right_watermark(image.content, image.mime)
    white_ratio = _white_background_ratio(image.content)
    checks = (
        CharacterImageCheck(
            "WATERMARK_FREE",
            "PASSED" if watermark_free else "FAILED",
            1.0 if watermark_free else 0.0,
            "未检测到右下角水印" if watermark_free else "检测到疑似右下角水印",
        ),
        CharacterImageCheck(
            "FOREGROUND_CLEAR",
            "REVIEW_REQUIRED",
            None,
            "自动视觉质检不可用，请确认前景没有遮挡人物主体",
        ),
        CharacterImageCheck(
            "BODY_CONTINUITY",
            "REVIEW_REQUIRED",
            None,
            "自动视觉质检不可用，请确认身体与四肢结构连续",
        ),
        CharacterImageCheck(
            "PURE_WHITE_BACKGROUND",
            (
                "PASSED"
                if white_ratio is not None and white_ratio >= 0.98
                else "REVIEW_REQUIRED"
            ),
            white_ratio,
            (
                "画面边缘符合纯白背景要求"
                if white_ratio is not None and white_ratio >= 0.98
                else "请人工确认人物轮廓外为均匀纯白背景"
            ),
        ),
    )
    status = "FAILED" if any(item.status == "FAILED" for item in checks) else "REVIEW_REQUIRED"
    return CharacterImageQualityReport(
        status=status,
        provider="rules-manual-gate",
        model=None,
        checks=checks,
    )


def _parse_visual_report(text: str) -> dict[str, dict[str, object]]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("quality response is not JSON")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("quality response is not an object")
    result: dict[str, dict[str, object]] = {}
    for key in ("watermark", "foreground_occlusion", "body_continuity", "pure_white_background"):
        item = payload.get(key)
        if not isinstance(item, dict) or not isinstance(item.get("passed"), bool):
            raise ValueError(f"{key} result is missing")
        confidence = item.get("confidence")
        if not isinstance(confidence, int | float):
            raise ValueError(f"{key} confidence is missing")
        result[key] = {
            "passed": item["passed"],
            "confidence": min(1.0, max(0.0, float(confidence))),
            "reason": str(item.get("reason") or "视觉质检已完成").strip()[:160],
        }
    return result


async def evaluate_character_image_quality(
    settings: Settings,
    image: GeneratedImage,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CharacterImageQualityReport:
    if image.model == "deterministic-image-v1":
        checks = tuple(
            CharacterImageCheck(item, "PASSED", 1.0, "确定性模拟资产通过测试规则")
            for item in CHARACTER_IMAGE_CHECK_TYPES
        )
        return CharacterImageQualityReport(
            status="PASSED",
            provider="deterministic-rules",
            model=image.model,
            checks=checks,
        )
    if not settings.ark_api_key:
        return _manual_report(image)

    instruction = (
        "你是角色设定图生成后质检员。只审核这张图片，并分别判断："
        "1. watermark：画面是否没有任何水印、文字、Logo、签名、角标；"
        "2. foreground_occlusion：人物脸部、躯干和关键肢体或数字实体主体是否没有被前景物体遮挡；"
        "3. body_continuity：头颈、躯干、手臂、手、腿等身体结构是否自然连续，"
        "没有断裂、融合、重复或异常缺失；若主体本来没有人体，改为检查其视觉结构连续，"
        "不得仅因没有人体而判失败；"
        "4. pure_white_background：人物轮廓外是否为均匀 #FFFFFF 纯白背景，"
        "没有渐变、纹理、阴影、环境或反射。"
        "每项 passed=true 表示符合要求。只输出 JSON，不要 Markdown："
        '{"watermark":{"passed":true,"confidence":0.0,"reason":"中文理由"},'
        '"foreground_occlusion":{"passed":true,"confidence":0.0,"reason":"中文理由"},'
        '"body_continuity":{"passed":true,"confidence":0.0,"reason":"中文理由"},'
        '"pure_white_background":{"passed":true,"confidence":0.0,"reason":"中文理由"}}'
    )
    content = [
        {"type": "input_text", "text": instruction},
        {"type": "input_image", "image_url": image_data_url(image.content, image.mime)},
    ]
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
                raise ValueError("quality response text missing")
            parsed = _parse_visual_report(output)
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        return _manual_report(image)

    mapping = (
        ("WATERMARK_FREE", "watermark"),
        ("FOREGROUND_CLEAR", "foreground_occlusion"),
        ("BODY_CONTINUITY", "body_continuity"),
        ("PURE_WHITE_BACKGROUND", "pure_white_background"),
    )
    checks = tuple(
        CharacterImageCheck(
            check_type=check_type,
            status="PASSED" if bool(parsed[key]["passed"]) else "FAILED",
            score=float(parsed[key]["confidence"]),
            message=str(parsed[key]["reason"]),
        )
        for check_type, key in mapping
    )
    return CharacterImageQualityReport(
        status="PASSED" if all(item.status == "PASSED" for item in checks) else "FAILED",
        provider="volcengine-ark",
        model=settings.ark_prompt_model,
        checks=checks,
    )
