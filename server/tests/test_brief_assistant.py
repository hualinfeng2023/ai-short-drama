import json
from dataclasses import replace

import httpx
import pytest

from app.config import get_settings
from app.services.brief_assistant import (
    _call_ark,
    _call_ark_avoidances,
    suggest_brief_avoidances,
    suggest_brief_requirements,
)

pytestmark = pytest.mark.anyio

BRIEF = {
    "idea": "一对姐妹得到可以放大和缩小物体的药丸，在末日中走向不同选择。",
    "genre": "urban_drama",
    "style": "realistic_cinematic",
    "target_duration_sec": 60,
    "aspect_ratio": "9:16",
    "target_platform": "douyin",
    "narrative_protagonist": "dual",
    "target_audience": "general",
    "emotional_rewards": ["family"],
    "audience_profile": "",
    "production_format": "live_action",
    "primary_market": "CN",
    "canonical_language": "zh-CN",
    "existing_requirements": ["前三秒建立明确危机、异常事件或人物目标"],
    "content_avoidances": [],
}


async def test_brief_requirements_use_contextual_local_fallback_without_key() -> None:
    result = await suggest_brief_requirements(
        BRIEF,
        settings=replace(get_settings(), ark_api_key=None),
    )

    assert result.provider == "local-fallback"
    assert result.warning == "ARK_API_KEY 未配置"
    assert "前三秒建立明确危机、异常事件或人物目标" not in result.items
    assert "核心人物、字幕与关键动作保持在竖屏安全区域" in result.items
    assert any("60秒" in item for item in result.items)


async def test_brief_requirements_ark_contract_returns_structured_unique_items() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "items": [
                            "前三秒明确姐妹获得药丸后的危机",
                            "两次人生选择必须形成可见对照",
                            "放大与缩小能力的使用规则保持一致",
                            "完整叙事控制在60秒内",
                        ]
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
    assert len(result.items) == 4
    assert "existing_requirements" in str(captured["input"])
    assert "主角性别只定义叙事视角" in str(captured["input"])


async def test_brief_avoidances_use_contextual_local_fallback_and_dedupe() -> None:
    brief = {
        **BRIEF,
        "content_requirements": ["前三秒建立危机"],
        "existing_avoidances": ["避免未经授权的品牌、音乐、肖像或素材露出"],
    }

    result = await suggest_brief_avoidances(
        brief,
        settings=replace(get_settings(), ark_api_key=None),
    )

    assert result.provider == "local-fallback"
    assert result.warning == "ARK_API_KEY 未配置"
    assert "避免未经授权的品牌、音乐、肖像或素材露出" not in result.items
    assert "避免字幕、人物表情或关键动作超出竖屏安全区域" in result.items
    assert any("人物身份" in item for item in result.items)


async def test_brief_avoidances_ark_contract_returns_structured_unique_items() -> None:
    captured: dict[str, object] = {}
    brief = {
        **BRIEF,
        "content_requirements": ["前三秒建立危机"],
        "existing_avoidances": ["避免未授权品牌露出"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "items": [
                            "避免未授权品牌露出",
                            "避免姐妹能力规则前后矛盾",
                            "避免无铺垫新增第三颗药丸解决冲突",
                            "避免字幕和关键动作超出竖屏安全区域",
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        )

    result = await _call_ark_avoidances(
        replace(get_settings(), ark_api_key="test-key"),
        brief,
        transport=httpx.MockTransport(handler),
    )

    assert result.provider == "volcengine-ark"
    assert len(result.items) == 3
    assert "existing_avoidances" in str(captured["input"])
    assert "不要过度限制合理创意" in str(captured["input"])
