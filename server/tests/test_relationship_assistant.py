import json
from dataclasses import replace

import pytest
from app.config import get_settings
from app.services.relationship_assistant import generate_upbringing_suggestion
from app.services.text_provider import TextGenerationResult

pytestmark = pytest.mark.anyio


CONTEXT = {
    "relationship_key": "daughter-mother",
    "source_character": {
        "key": "daughter",
        "name": "林微",
        "role": "叙事主角，女儿",
        "occupation": "广告公司职员",
    },
    "target_character": {
        "key": "mother",
        "name": "刘秀英",
        "role": "共同主角，母亲",
        "occupation": "社区食堂工作人员",
    },
    "family_kinship": {
        "relation_type": "BIOLOGICAL_PARENT_CHILD",
        "shared_upbringing": "UNKNOWN",
        "upbringing_context": None,
    },
    "surface_relationship": "普通母女，女儿嫌弃母亲职业普通，母亲心疼女儿职场辛苦",
    "true_relationship": "母亲独自把女儿养大，女儿尚不了解母亲过去的职业经历",
}


async def test_upbringing_suggestion_uses_grounded_local_fallback_without_key() -> None:
    result = await generate_upbringing_suggestion(
        CONTEXT,
        settings=replace(get_settings(), ark_api_key=None),
    )

    assert result.provider == "local-fallback"
    assert result.model == "relationship-upbringing-v1"
    assert result.warning is not None and "ARK_API_KEY 未配置" in result.warning
    assert "林微与刘秀英属于亲生父母与子女关系" in result.suggestion
    assert "共同成长环境尚未确认" in result.suggestion
    assert "普通母女" in result.suggestion


async def test_upbringing_suggestion_ai_prompt_preserves_typed_kinship(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_ark_json(settings, *, prompt, validator):  # noqa: ANN001
        del settings
        captured["prompt"] = prompt
        captured["validator"] = validator
        return TextGenerationResult(
            payload={
                "suggestion": (
                    "现有设定确认林微与刘秀英是亲生母女，但共同成长环境尚待确认。"
                    "母亲对女儿职场处境的心疼与女儿对母亲职业的偏见，形成了长期的表达错位。"
                )
            },
            provider="volcengine-ark",
            model="test-model",
            request_id="request-1",
            repair_attempts=0,
        )

    monkeypatch.setattr(
        "app.services.relationship_assistant._ark_json",
        fake_ark_json,
    )
    result = await generate_upbringing_suggestion(
        CONTEXT,
        settings=replace(get_settings(), ark_api_key="test-key"),
    )

    prompt = str(captured["prompt"])
    assert captured["validator"].__name__ == "UpbringingSuggestionOutput"
    assert "非血缘亲属不得写成亲生血缘" in prompt
    assert "共同成长环境为“尚不明确”时" in prompt
    assert json.dumps(CONTEXT, ensure_ascii=False) in prompt
    assert result.provider == "volcengine-ark"
    assert result.warning is None
    assert "共同成长环境尚待确认" in result.suggestion
