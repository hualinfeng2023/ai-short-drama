import base64
import json
import math
from dataclasses import dataclass
from io import BytesIO

import httpx
from PIL import Image, ImageChops, ImageOps, ImageStat, UnidentifiedImageError

from app.config import Settings
from app.services.identity_consistency import _output_text, image_data_url
from app.services.image_provider import GeneratedImage

CHARACTER_IMAGE_CHECK_TYPES = (
    "WATERMARK_FREE",
    "FOREGROUND_CLEAR",
    "BODY_CONTINUITY",
    "PURE_WHITE_BACKGROUND",
)
DISTINCT_IDENTITY_COPY_THRESHOLD = 0.94
CONTACT_SHADOW_LOCATIONS = ("脚下", "脚部下方", "身体下方", "主体下方", "襁褓下方")
CONTACT_SHADOW_DISALLOWED = (
    "大面积",
    "明显阴影",
    "浓重",
    "深色",
    "拖长",
    "渐变",
    "纹理",
    "环境",
    "灰白背景",
    "有色背景",
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


def _decode_image_data_url(value: str) -> bytes | None:
    if not value.startswith("data:image/") or ";base64," not in value:
        return None
    try:
        return base64.b64decode(value.split(";base64,", 1)[1], validate=True)
    except (ValueError, TypeError):
        return None


def _near_duplicate_similarity(left: bytes, right: bytes) -> float | None:
    left_image = _open_rgb(left)
    right_image = _open_rgb(right)
    if left_image is None or right_image is None:
        return None
    size = (256, 256)
    left_image = ImageOps.fit(left_image, size, Image.Resampling.LANCZOS)
    right_image = ImageOps.fit(right_image, size, Image.Resampling.LANCZOS)
    difference = ImageChops.difference(left_image, right_image)
    channel_rms = ImageStat.Stat(difference).rms
    normalized_rmse = (
        math.sqrt(sum(value * value for value in channel_rms) / len(channel_rms)) / 255
    )
    return max(0.0, min(1.0, 1.0 - normalized_rmse))


def _append_distinct_identity_check(
    report: CharacterImageQualityReport,
    image: GeneratedImage,
    reference_images: list[str] | None,
) -> CharacterImageQualityReport:
    if not reference_images:
        return report
    scores = [
        score
        for value in reference_images
        if (content := _decode_image_data_url(value)) is not None
        if (score := _near_duplicate_similarity(image.content, content)) is not None
    ]
    if not scores:
        return report
    similarity = max(scores)
    copied = similarity >= DISTINCT_IDENTITY_COPY_THRESHOLD
    check = CharacterImageCheck(
        "DISTINCT_IDENTITY",
        "FAILED" if copied else "PASSED",
        similarity,
        (
            "生成结果与亲属证据图近似复制，已阻止进入可选候选"
            if copied
            else "生成结果未近似复制亲属证据图"
        ),
    )
    return CharacterImageQualityReport(
        status="FAILED" if copied else report.status,
        provider=report.provider,
        model=report.model,
        checks=(*report.checks, check),
    )


def _is_permitted_contact_shadow(reason: str) -> bool:
    return (
        "阴影" in reason
        and any(item in reason for item in CONTACT_SHADOW_LOCATIONS)
        and not any(item in reason for item in CONTACT_SHADOW_DISALLOWED)
    )


def apply_character_image_quality_policy(
    report: CharacterImageQualityReport,
) -> CharacterImageQualityReport:
    checks = tuple(
        CharacterImageCheck(
            check.check_type,
            "PASSED",
            check.score,
            "仅存在紧贴主体的低对比度自然接触阴影，按当前纯白背景规则允许",
        )
        if (
            check.check_type == "PURE_WHITE_BACKGROUND"
            and check.status == "FAILED"
            and _is_permitted_contact_shadow(check.message)
        )
        else check
        for check in report.checks
    )
    return CharacterImageQualityReport(
        status="PASSED" if all(check.status == "PASSED" for check in checks) else report.status,
        provider=report.provider,
        model=report.model,
        checks=checks,
    )


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
        image.crop((0, image.height - border_width, image.width, image.height)).get_flattened_data()
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
            ("PASSED" if white_ratio is not None and white_ratio >= 0.98 else "REVIEW_REQUIRED"),
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
    distinct_identity_reference_images: list[str] | None = None,
    quality_context: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CharacterImageQualityReport:
    if image.model == "deterministic-image-v1":
        checks = tuple(
            CharacterImageCheck(item, "PASSED", 1.0, "确定性模拟资产通过测试规则")
            for item in CHARACTER_IMAGE_CHECK_TYPES
        )
        return _append_distinct_identity_check(
            CharacterImageQualityReport(
                status="PASSED",
                provider="deterministic-rules",
                model=image.model,
                checks=checks,
            ),
            image,
            distinct_identity_reference_images,
        )
    if not settings.ark_api_key:
        return _append_distinct_identity_check(
            _manual_report(image),
            image,
            distinct_identity_reference_images,
        )

    instruction = (
        "你是角色设定图生成后质检员。只审核这张图片，并分别判断："
        "1. watermark：画面是否没有任何水印、文字、Logo、签名、角标；"
        "2. foreground_occlusion：人物脸部、躯干和关键肢体或数字实体主体是否没有被前景物体遮挡；"
        "3. body_continuity：头颈、躯干、手臂、手、腿等身体结构是否自然连续，"
        "没有断裂、融合、重复或异常缺失；若主体本来没有人体，改为检查其视觉结构连续，"
        "不得仅因没有人体而判失败；"
        "4. pure_white_background：人物轮廓外是否为均匀 #FFFFFF 纯白背景，"
        "没有渐变、纹理、环境或反射。允许脚底、身体或襁褓正下方紧贴主体、"
        "范围小且低对比度的自然接触阴影，不得仅因此判失败；"
        "大面积、明显或拖长的投影仍应判失败。"
        "每项 passed=true 表示符合要求。只输出 JSON，不要 Markdown："
        '{"watermark":{"passed":true,"confidence":0.0,"reason":"中文理由"},'
        '"foreground_occlusion":{"passed":true,"confidence":0.0,"reason":"中文理由"},'
        '"body_continuity":{"passed":true,"confidence":0.0,"reason":"中文理由"},'
        '"pure_white_background":{"passed":true,"confidence":0.0,"reason":"中文理由"}}'
    )
    if (quality_context or "").upper().startswith("INFANT_"):
        instruction += (
            "当前主体是新生儿或婴儿。婴儿可以自然平躺或被襁褓包裹；"
            "躯干、腿和脚被襁褓合理遮蔽属于正常情况，不得仅因腿脚未露出而将 "
            "body_continuity 判为失败。应检查可见身体部位和襁褓外轮廓是否符合生理结构，"
            "只有出现肢体从襁褓外异常突出、重复、融合、断裂或不可能的轮廓时才判失败。"
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
        return _append_distinct_identity_check(
            _manual_report(image),
            image,
            distinct_identity_reference_images,
        )

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
    return _append_distinct_identity_check(
        apply_character_image_quality_policy(
            CharacterImageQualityReport(
                status="PASSED" if all(item.status == "PASSED" for item in checks) else "FAILED",
                provider="volcengine-ark",
                model=settings.ark_prompt_model,
                checks=checks,
            )
        ),
        image,
        distinct_identity_reference_images,
    )
