import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings, get_settings
from app.domain.narrative_targeting import EmotionalReward
from app.schemas import BriefEmotionalRewardSuggestionRead


class EmotionalRewardPayload(BaseModel):
    reward: EmotionalReward
    rationale: str = Field(min_length=2, max_length=80)


@dataclass(frozen=True)
class GeneratedEmotionalReward:
    reward: EmotionalReward
    rationale: str
    provider: str
    model: str


class EmotionalRewardSuggestionError(Exception):
    pass


_REWARD_RULES: tuple[tuple[EmotionalReward, tuple[str, ...]], ...] = (
    ("family", ("孩子", "婴儿", "胚胎", "父亲", "母亲", "夫妻", "姐妹", "兄弟", "亲子", "家庭")),
    ("romance", ("爱情", "恋爱", "婚礼", "情侣", "爱人", "相爱", "前任")),
    ("career", ("裁员", "职场", "公司", "工作室", "客户", "创业", "事业", "升职")),
    ("revenge", ("复仇", "背叛", "陷害", "清算", "夺回", "报仇")),
    ("power", ("权力", "权谋", "朝堂", "王位", "宫廷", "控制权", "夺权")),
    ("public_mission", ("社区", "撤离", "救援", "公共", "灾难", "守护城市", "拯救人类")),
    ("identity", ("身份", "冒名", "认亲", "身世", "真实自我", "自我选择", "成长")),
)

_REWARD_LABELS: dict[EmotionalReward, str] = {
    "romance": "爱情",
    "identity": "身份",
    "career": "事业",
    "revenge": "复仇",
    "family": "亲情",
    "power": "权力",
    "public_mission": "公共使命",
}


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


def _local_suggestion(idea: str) -> GeneratedEmotionalReward:
    compact = re.sub(r"\s+", "", idea)
    best_reward: EmotionalReward = "identity"
    best_score = 0
    for reward, keywords in _REWARD_RULES:
        score = sum(compact.count(keyword) for keyword in keywords)
        if score > best_score:
            best_reward = reward
            best_score = score
    label = _REWARD_LABELS[best_reward]
    return GeneratedEmotionalReward(
        reward=best_reward,
        rationale=f"故事的核心选择最直接兑现{label}回报",
        provider="local-fallback",
        model="emotional-reward-recommender-v1",
    )


def _provider_prompt(brief: dict[str, Any]) -> str:
    return f"""你是短剧开发策划。请根据故事本身，从下列情绪回报中只选择一项作为可修改的初始建议：
romance（爱情）、identity（身份）、career（事业）、revenge（复仇）、
family（亲情）、power（权力）、public_mission（公共使命）。

要求：
1. 只判断故事结尾最主要兑现给观众的情绪回报，不扩写或改写故事。
2. 不得根据人物性别、叙事主角或目标受众推断选择；这些字段与情绪回报彼此独立。
3. 即使故事包含多种情感，也只能返回最核心的一项。
4. rationale 用一句不超过 40 个汉字的中文说明，必须锚定故事中的核心选择或结局。
5. 严格返回 JSON：{{"reward":"family","rationale":"说明"}}，不要 Markdown 或其他文字。

故事想法：
{brief["idea"]}

题材：
{brief.get("genre", "未指定")}
"""


async def _call_ark(
    settings: Settings,
    brief: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeneratedEmotionalReward:
    if not settings.ark_api_key:
        raise EmotionalRewardSuggestionError("ARK_API_KEY 未配置")
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
            decoded = json.loads(
                (output or "").removeprefix("```json").removesuffix("```").strip()
            )
            payload = EmotionalRewardPayload.model_validate(decoded)
    except (httpx.HTTPError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise EmotionalRewardSuggestionError("火山方舟情绪回报建议暂时不可用") from exc
    return GeneratedEmotionalReward(
        reward=payload.reward,
        rationale=payload.rationale.strip(),
        provider="volcengine-ark",
        model=settings.ark_prompt_model,
    )


async def suggest_emotional_reward(
    brief: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> BriefEmotionalRewardSuggestionRead:
    resolved_settings = settings or get_settings()
    try:
        result = await _call_ark(resolved_settings, brief)
        warning = None
    except EmotionalRewardSuggestionError as exc:
        result = _local_suggestion(str(brief["idea"]))
        warning = str(exc)
    return BriefEmotionalRewardSuggestionRead(
        reward=result.reward,
        rationale=result.rationale,
        provider=result.provider,
        model=result.model,
        warning=warning,
    )
