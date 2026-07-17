import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.domain.narrative_targeting import targeting_prompt_guardrails
from app.schemas import ProjectNameSuggestionRead


@dataclass(frozen=True)
class GeneratedProjectName:
    text: str
    provider: str
    model: str


class ProjectNamingError(Exception):
    pass


def _extract_output_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict):
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return str(part["text"]).strip()
    return None


def _clean_name(value: str) -> str | None:
    first_line = value.strip().splitlines()[0] if value.strip() else ""
    cleaned = re.sub(r"^(项目名称|片名|剧名|标题)\s*[：:]\s*", "", first_line)
    cleaned = cleaned.strip(" \t\"'“”‘’《》【】[]。！!？?，,")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned[:24] if 2 <= len(cleaned) <= 40 else None


def _local_name(idea: str) -> str:
    compact = re.sub(r"\s+", "", idea)
    semantic_rules = (
        (("姐妹", "神药"), "双生神药"),
        (("暴雨", "停电", "秘密"), "雨夜秘密"),
        (("便利店", "秘密"), "便利店的秘密"),
        (("婚礼", "沉默"), "婚礼前的沉默"),
        (("工作室", "钥匙"), "旧钥匙新人生"),
        (("母亲", "女儿"), "母女之间"),
        (("失踪", "照片"), "照片里消失的人"),
    )
    for required, title in semantic_rules:
        if all(token in compact for token in required):
            return title

    first_clause = re.split(r"[。！？；，,!?;]", compact, maxsplit=1)[0]
    reduced = re.sub(
        r"^(一个|一位|一名|一对|女主|男主|主人公|主角)|"
        r"(突然|同时|意外|开始|发现|得到|遭遇|之后|以后|面临|决定|最终)",
        "",
        first_clause,
    )
    reduced = re.sub(r"[的了着过在和与及又两颗一场]", "", reduced)
    if len(reduced) >= 4:
        return reduced[:10]
    return (first_clause or "未命名故事")[:10]


def _provider_prompt(brief: dict[str, Any]) -> str:
    return f"""你是中文短剧平台的资深策划，请为下面的创作 Brief 生成一个项目名称。

要求：
1. 准确抓住核心人物关系、关键意象或戏剧冲突，不虚构 Brief 之外的事实。
2. 中文名称优先控制在 4 至 12 个汉字，简洁、易记、有短剧传播力，但不要标题党。
3. 避免“粗略故事梗概”“未命名项目”“我的短剧”等占位表达。
4. 不要使用书名号、引号、标点、编号、副标题或解释，只输出一个最终名称。
5. 不得根据主角性别或目标受众套用类型化标题。

{targeting_prompt_guardrails(brief)}

当前名称：{brief.get("current_name") or "无"}
故事想法：{brief["idea"]}
题材：{brief.get("genre", "未指定")}
视觉风格：{brief.get("style", "未指定")}
目标受众：{brief.get("target_audience", "general")}
补充受众画像：{brief.get("audience_profile") or "未指定"}
主要市场：{brief.get("primary_market", "CN")}
规范语言：{brief.get("canonical_language", "zh-CN")}
"""


async def _call_ark(
    settings: Settings,
    brief: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeneratedProjectName:
    if not settings.ark_api_key:
        raise ProjectNamingError("ARK_API_KEY 未配置")
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
    except (httpx.HTTPError, ValueError) as exc:
        raise ProjectNamingError("火山方舟智能命名暂时不可用") from exc
    cleaned = _clean_name(output or "")
    if cleaned is None:
        raise ProjectNamingError("火山方舟未返回可用的项目名称")
    return GeneratedProjectName(
        text=cleaned,
        provider="volcengine-ark",
        model=settings.ark_prompt_model,
    )


async def suggest_project_name(
    brief: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> ProjectNameSuggestionRead:
    resolved_settings = settings or get_settings()
    try:
        result = await _call_ark(resolved_settings, brief)
        warning = None
    except ProjectNamingError as exc:
        result = GeneratedProjectName(
            text=_local_name(str(brief["idea"])),
            provider="local-fallback",
            model="brief-name-generator-v1",
        )
        warning = str(exc)
    return ProjectNameSuggestionRead(
        original=str(brief.get("current_name") or "") or None,
        suggested=result.text,
        provider=result.provider,
        model=result.model,
        warning=warning,
    )
