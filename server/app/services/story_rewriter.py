import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings, get_settings
from app.domain.narrative_targeting import targeting_prompt_guardrails
from app.schemas import BriefStoryRewriteRead


class StoryRewritePayload(BaseModel):
    rewritten: str = Field(min_length=10, max_length=4000)
    logic_checks: list[str] = Field(min_length=3, max_length=6)
    unsupported_additions: list[str] = Field(default_factory=list, max_length=0)


@dataclass(frozen=True)
class GeneratedStoryRewrite:
    rewritten: str
    logic_checks: list[str]
    provider: str
    model: str


class StoryRewriteError(Exception):
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


def _provider_prompt(brief: dict[str, Any]) -> str:
    return f"""你是资深短剧编剧与剧本编辑。你的唯一任务是重构用户已有 Brief 的叙事表达，
让逻辑严谨、因果清晰、故事线易于继续拆解为剧本；你不是续写作者。

事实源优先级：
1. `idea` 中明确写出的事实，是唯一剧情事实源。
2. 题材、风格、时长、画幅、平台、叙事主角、目标受众、情绪回报、补充受众画像、
市场和语言只用于校准表达，不能转化为新剧情；这些字段之间也不得相互推断。
3. `content_requirements` 和 `content_avoidances` 是制作约束，不是可新增的剧情事实。

硬性规则：
1. 不新增、删除、合并或替换核心人物、人物关系、身份、能力、关键道具、地点、
时间跨度、秘密、冲突、选择、结局与世界规则。
2. 只能重排已有信息、消除指代歧义、压缩重复内容，并把原文明确存在的因果关系
表达清楚；原文没有交代的原因必须保留未知，不能推断补齐。
3. 按“核心前提 → 触发事件 → 冲突升级与人物选择 → 后果/高潮 → 已有结尾承诺”
组织；某一环节在原文缺失时直接省略，不能编造。
4. 保持用户指定的规范语言；不要输出对白、分镜、镜头语言、营销口号或创作解释。
5. `logic_checks` 用 3 至 6 条简短文字说明人物、因果、时间线和结尾如何保持原 Brief 一致。
6. 如果任何内容无法在原 Brief 中找到依据，必须放入 `unsupported_additions`；
最终合格输出要求该数组为空。
7. 严格返回 JSON：
{{"rewritten":"...","logic_checks":["..."],"unsupported_additions":[]}}
不要 Markdown。

{targeting_prompt_guardrails(brief)}

当前 Brief：
{json.dumps(brief, ensure_ascii=False)}
"""


async def _call_ark(
    settings: Settings,
    brief: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeneratedStoryRewrite:
    if not settings.ark_api_key:
        raise StoryRewriteError("ARK_API_KEY 未配置，无法调用 Doubao Seed")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(min(settings.ark_request_timeout_seconds, 30)),
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
            payload = StoryRewritePayload.model_validate(decoded)
    except (httpx.HTTPError, ValueError, ValidationError) as exc:
        raise StoryRewriteError("Doubao Seed 叙事重构暂时不可用或返回内容不合格") from exc

    rewritten = payload.rewritten.strip()
    if not rewritten:
        raise StoryRewriteError("Doubao Seed 未返回可用的叙事内容")
    return GeneratedStoryRewrite(
        rewritten=rewritten,
        logic_checks=[item.strip() for item in payload.logic_checks if item.strip()],
        provider="volcengine-ark",
        model=settings.ark_prompt_model,
    )


async def rewrite_story_idea(
    brief: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> BriefStoryRewriteRead:
    result = await _call_ark(settings or get_settings(), brief)
    return BriefStoryRewriteRead(
        original=str(brief["idea"]),
        rewritten=result.rewritten,
        logic_checks=result.logic_checks,
        provider=result.provider,
        model=result.model,
    )
