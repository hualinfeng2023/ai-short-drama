import json
from dataclasses import replace

import httpx
import pytest

from app.config import get_settings
from app.services.emotional_reward_suggestion import (
    _call_ark,
    suggest_emotional_reward,
)

pytestmark = pytest.mark.anyio

BRIEF = {
    "idea": "一对永生夫妻必须决定谁放弃永生，才能换取孩子出生。",
    "genre": "sci_fi",
}


async def test_local_fallback_recommends_one_story_based_reward() -> None:
    result = await suggest_emotional_reward(
        BRIEF,
        settings=replace(get_settings(), ark_api_key=None),
    )

    assert result.reward == "family"
    assert result.provider == "local-fallback"
    assert result.warning == "ARK_API_KEY 未配置"


async def test_ark_contract_excludes_audience_and_protagonist_inference() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "reward": "family",
                        "rationale": "夫妻用永生交换孩子出生",
                    },
                    ensure_ascii=False,
                )
            },
        )

    result = await _call_ark(
        replace(get_settings(), ark_api_key="test-key"),
        BRIEF,
        transport=httpx.MockTransport(handler),
    )

    assert result.reward == "family"
    prompt = str(captured["input"])
    assert "不得根据人物性别、叙事主角或目标受众推断" in prompt
    assert "目标受众：" not in prompt
    assert "叙事主角：" not in prompt
