import json
from dataclasses import replace

import httpx
import pytest

from app.config import get_settings
from app.services.story_rewriter import StoryRewriteError, _call_ark

pytestmark = pytest.mark.anyio

BRIEF = {
    "idea": (
        "一对姐妹同时得到两颗神药，一颗能把东西变大，一颗能把东西变小。"
        "七天后末日来临，姐姐用能力囤积食物，妹妹把男友赶出门外。"
        "走投无路的妹妹回到家，却被姐姐从楼上推下。"
    ),
    "genre": "urban_drama",
    "style": "realistic_cinematic",
    "target_duration_sec": 60,
    "aspect_ratio": "9:16",
    "target_platform": "douyin",
    "secondary_platforms": [],
    "narrative_protagonist": "dual",
    "target_audience": "general",
    "emotional_rewards": ["family"],
    "audience_profile": "",
    "production_format": "live_action",
    "primary_market": "CN",
    "secondary_markets": [],
    "canonical_language": "zh-CN",
    "localization_targets": [],
    "content_requirements": ["前三秒建立危机"],
    "content_avoidances": ["未授权品牌露出"],
}


async def test_story_rewriter_calls_seed_with_full_brief_and_structured_guard() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "rewritten": (
                            "七天后末日将至，一对姐妹同时得到两颗神药：一颗能让物体变大，"
                            "另一颗能让物体变小。姐姐选择利用能力囤积食物；妹妹则把男友赶出门外。"
                            "当妹妹走投无路返回家中，姐姐最终将她从楼上推下。"
                        ),
                        "logic_checks": [
                            "保留姐妹与神药的核心设定",
                            "保留七天后的时间顺序",
                            "保留两人的选择及最终后果",
                        ],
                        "unsupported_additions": [],
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

    assert result.provider == "volcengine-ark"
    assert result.model == get_settings().ark_prompt_model
    assert len(result.logic_checks) == 3
    assert captured["model"] == get_settings().ark_prompt_model
    assert "不新增、删除、合并或替换核心人物" in str(captured["input"])
    assert "这些字段之间也不得相互推断" in str(captured["input"])
    assert "content_avoidances" in str(captured["input"])


async def test_story_rewriter_rejects_declared_unsupported_additions() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "rewritten": "姐妹得到神药后，又遇到一名从未在原文出现的科学家。",
                        "logic_checks": ["人物检查", "因果检查", "时间线检查"],
                        "unsupported_additions": ["新增科学家角色"],
                    },
                    ensure_ascii=False,
                )
            },
        )

    with pytest.raises(StoryRewriteError):
        await _call_ark(
            replace(get_settings(), ark_api_key="test-key"),
            BRIEF,
            transport=httpx.MockTransport(handler),
        )
