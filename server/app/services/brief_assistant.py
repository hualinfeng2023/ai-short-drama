import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings, get_settings
from app.domain.narrative_targeting import targeting_prompt_guardrails
from app.schemas import BriefAvoidancesSuggestionRead, BriefRequirementsSuggestionRead


class RequirementsPayload(BaseModel):
    items: list[str] = Field(min_length=3, max_length=6)


@dataclass(frozen=True)
class GeneratedRequirements:
    items: list[str]
    provider: str
    model: str


class BriefAssistantError(Exception):
    pass


def _extract_output_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return str(part["text"]).strip()
    return None


def _normalize_items(items: list[str], existing: list[str]) -> list[str]:
    existing_normalized = {item.strip() for item in existing if item.strip()}
    unique: list[str] = []
    for item in items:
        cleaned = item.strip().lstrip("-•0123456789.、 ").rstrip("。；; ")
        if not cleaned or cleaned in existing_normalized or cleaned in unique:
            continue
        unique.append(cleaned[:80])
    return unique[:6]


def _local_requirements(brief: dict[str, Any]) -> list[str]:
    duration = int(brief.get("target_duration_sec", 60))
    aspect_ratio = str(brief.get("aspect_ratio", "9:16"))
    platform = str(brief.get("target_platform", "douyin"))
    market = str(brief.get("primary_market", "CN"))
    candidates = [
        "前三秒建立明确危机、异常事件或人物目标",
        "每个关键转折必须由人物选择或行动推动",
        "核心人物关系与关键道具在前后镜头保持连续",
        f"完整叙事控制在{duration}秒内并保留清晰的起承转合",
        "结尾留下可延续下一集的反转或情绪钩子",
    ]
    if aspect_ratio == "9:16":
        candidates.insert(3, "核心人物、字幕与关键动作保持在竖屏安全区域")
    elif aspect_ratio == "16:9":
        candidates.insert(3, "利用横屏空间交代人物关系与环境信息")
    if market != "CN":
        candidates.append(f"对白、文化语境与内容表达适配{market}市场")
    elif platform in {"douyin", "kuaishou"}:
        candidates.append("节奏与信息密度适配中文移动端短视频观看")
    return _normalize_items(candidates, list(brief.get("existing_requirements", [])))


def _local_avoidances(brief: dict[str, Any]) -> list[str]:
    aspect_ratio = str(brief.get("aspect_ratio", "9:16"))
    platform = str(brief.get("target_platform", "douyin"))
    candidates = [
        "避免未经授权的品牌、音乐、肖像或素材露出",
        "避免人物身份、能力规则、时间线与关键道具前后矛盾",
        "避免为制造反转而新增 Brief 未设定的核心人物或世界规则",
        "避免依靠无铺垫的巧合或外力解决核心冲突",
        "避免仅为刺激而呈现血腥暴力或危险行为特写",
    ]
    if aspect_ratio == "9:16":
        candidates.append("避免字幕、人物表情或关键动作超出竖屏安全区域")
    elif aspect_ratio == "16:9":
        candidates.append("避免将关键叙事信息只放在横屏画面边缘")
    if platform in {"douyin", "kuaishou"}:
        candidates.append("避免连续长段对白或静态空镜拖慢移动端节奏")
    return _normalize_items(candidates, list(brief.get("existing_avoidances", [])))


def _provider_prompt(brief: dict[str, Any]) -> str:
    return f"""你是短剧制片人与内容策略师。请根据 Brief 生成 4 至 6 条“必须满足”的制作要求。

要求：
1. 每条必须具体、可执行、可在剧本或成片审核时验证。
2. 覆盖开场钩子、人物行动、时长节奏、画幅平台、连续性与结尾承诺中的关键项。
3. 不虚构 Brief 之外的人物、情节或商业目标，不重复现有要求，不写“高质量”“有吸引力”等空泛措辞。
4. 不要生成“必须避免”的负向规则。
5. 严格返回 JSON：{{"items":["要求1","要求2"]}}，不要 Markdown 或解释。

{targeting_prompt_guardrails(brief)}

Brief：
{json.dumps(brief, ensure_ascii=False)}
"""


def _avoidances_provider_prompt(brief: dict[str, Any]) -> str:
    return f"""你是短剧制片人、连续性审校与内容风险编辑。
请根据 Brief 生成 4 至 6 条“必须避免”的制作约束。

要求：
1. 每条必须具体、可执行、可在剧本、分镜或成片审核时验证。
2. 优先覆盖版权与素材、人物和设定连续性、无铺垫反转、平台画幅安全区、危险内容与节奏风险。
3. 必须贴合当前题材、风格、时长、画幅、平台、用户与市场；不要重复现有规避项。
4. 不虚构 Brief 之外的事实，不把正向制作要求改写成负向句，不写“遵守平台规则”“避免低质量”等空泛措辞。
5. 不要过度限制合理创意；只给出对当前项目确有价值的约束。
6. 严格返回 JSON：{{"items":["规避项1","规避项2"]}}，不要 Markdown 或解释。

{targeting_prompt_guardrails(brief)}

Brief：
{json.dumps(brief, ensure_ascii=False)}
"""


async def _call_ark(
    settings: Settings,
    brief: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeneratedRequirements:
    if not settings.ark_api_key:
        raise BriefAssistantError("ARK_API_KEY 未配置")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(min(settings.ark_request_timeout_seconds, 20)),
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
                    "input": _provider_prompt(brief),
                    "thinking": {"type": "disabled"},
                },
            )
            response.raise_for_status()
            output = _extract_output_text(response.json())
            decoded = json.loads((output or "").removeprefix("```json").removesuffix("```").strip())
            payload = RequirementsPayload.model_validate(decoded)
    except (httpx.HTTPError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise BriefAssistantError("火山方舟 Brief 智能代写暂时不可用") from exc
    items = _normalize_items(payload.items, list(brief.get("existing_requirements", [])))
    if not items:
        raise BriefAssistantError("火山方舟没有返回新的可用要求")
    return GeneratedRequirements(
        items=items,
        provider="volcengine-ark",
        model=settings.ark_prompt_model,
    )


async def _call_ark_avoidances(
    settings: Settings,
    brief: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeneratedRequirements:
    if not settings.ark_api_key:
        raise BriefAssistantError("ARK_API_KEY 未配置")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(min(settings.ark_request_timeout_seconds, 20)),
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
                    "input": _avoidances_provider_prompt(brief),
                    "thinking": {"type": "disabled"},
                },
            )
            response.raise_for_status()
            output = _extract_output_text(response.json())
            decoded = json.loads((output or "").removeprefix("```json").removesuffix("```").strip())
            payload = RequirementsPayload.model_validate(decoded)
    except (httpx.HTTPError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise BriefAssistantError("火山方舟 Brief 智能建议暂时不可用") from exc
    items = _normalize_items(payload.items, list(brief.get("existing_avoidances", [])))
    if not items:
        raise BriefAssistantError("火山方舟没有返回新的可用规避项")
    return GeneratedRequirements(
        items=items,
        provider="volcengine-ark",
        model=settings.ark_prompt_model,
    )


async def suggest_brief_requirements(
    brief: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> BriefRequirementsSuggestionRead:
    resolved_settings = settings or get_settings()
    try:
        result = await _call_ark(resolved_settings, brief)
        warning = None
    except BriefAssistantError as exc:
        result = GeneratedRequirements(
            items=_local_requirements(brief),
            provider="local-fallback",
            model="brief-requirements-generator-v1",
        )
        warning = str(exc)
    return BriefRequirementsSuggestionRead(
        items=result.items,
        provider=result.provider,
        model=result.model,
        warning=warning,
    )


async def suggest_brief_avoidances(
    brief: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> BriefAvoidancesSuggestionRead:
    resolved_settings = settings or get_settings()
    try:
        result = await _call_ark_avoidances(resolved_settings, brief)
        warning = None
    except BriefAssistantError as exc:
        result = GeneratedRequirements(
            items=_local_avoidances(brief),
            provider="local-fallback",
            model="brief-avoidances-generator-v1",
        )
        warning = str(exc)
    return BriefAvoidancesSuggestionRead(
        items=result.items,
        provider=result.provider,
        model=result.model,
        warning=warning,
    )
