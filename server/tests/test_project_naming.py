import json
from dataclasses import replace

import httpx
import pytest
from app.config import get_settings
from app.services.project_naming import ProjectNamingError, _call_ark, suggest_project_name

pytestmark = pytest.mark.anyio

BRIEF = {
    "current_name": "粗略故事梗概",
    "idea": "一对姐妹同时得到两颗神药，她们必须在亲情和欲望之间做出选择。",
    "genre": "urban_drama",
    "style": "realistic_cinematic",
    "narrative_protagonist": "dual",
    "target_audience": "general",
    "emotional_rewards": ["family"],
    "audience_profile": "",
    "production_format": "live_action",
    "primary_market": "CN",
    "canonical_language": "zh-CN",
}


async def test_project_name_uses_local_brief_fallback_without_key() -> None:
    result = await suggest_project_name(
        BRIEF,
        settings=replace(get_settings(), ark_api_key=None),
    )

    assert result.suggested == "双生神药"
    assert result.provider == "local-fallback"
    assert result.model == "brief-name-generator-v1"
    assert result.warning == "ARK_API_KEY 未配置"


async def test_project_name_ark_contract_returns_one_clean_title() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={"output_text": "《两颗神药》"},
        )

    result = await _call_ark(
        replace(get_settings(), ark_api_key="test-key"),
        BRIEF,
        transport=httpx.MockTransport(handler),
    )

    assert result.text == "两颗神药"
    assert result.provider == "volcengine-ark"
    assert "一对姐妹" in str(captured["input"])
    assert "先在内部生成至少 5 个候选" in str(captured["input"])
    assert "禁止原样返回当前名称" in str(captured["input"])
    assert "不得根据主角性别或目标受众套用类型化标题" in str(captured["input"])


async def test_project_name_rejects_current_name_as_the_candidate() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output_text": "粗略故事梗概"})

    with pytest.raises(ProjectNamingError, match="当前名称"):
        await _call_ark(
            replace(get_settings(), ark_api_key="test-key"),
            BRIEF,
            transport=httpx.MockTransport(handler),
        )


async def test_project_name_rejects_candidate_without_story_anchor() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output_text": "璀璨余生"})

    with pytest.raises(ProjectNamingError, match="核心人物、冲突或意象"):
        await _call_ark(
            replace(get_settings(), ark_api_key="test-key"),
            BRIEF,
            transport=httpx.MockTransport(handler),
        )


async def test_interactive_project_name_does_not_use_local_fallback() -> None:
    with pytest.raises(ProjectNamingError, match="ARK_API_KEY 未配置"):
        await suggest_project_name(
            BRIEF,
            settings=replace(get_settings(), ark_api_key=None),
            allow_fallback=False,
        )
