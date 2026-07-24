import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.domain.narrative_targeting import TOPIC_SLATE_MIX, reject_unrequested_stereotypes
from app.services.text_provider import (
    EpisodeScriptDraft,
    NarrativeReview,
    RoutedTextProvider,
    ScriptPackageOutput,
    StoryDirection,
    StoryFoundation,
    StoryPackage,
    StoryStructure,
    TextGenerationResult,
    TextProviderError,
    _ark_json,
    _ark_stream_output,
    assemble_story_package,
    deterministic_directions,
    deterministic_story_package,
    deterministic_story_structure,
    normalize_story_structure_payload,
    validate_script_package_relationship_contract,
)

pytestmark = pytest.mark.anyio


def _sse_event(event_type: str, payload: dict[str, object]) -> bytes:
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"
    ).encode()


def test_story_structure_normalizer_repairs_mechanical_relationship_invariants() -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    payload = deterministic_story_structure(BRIEF, direction).model_dump(mode="json")
    graph = payload["relationship_graph"]
    first = graph["beats"][0]
    first["sequence"] = 2
    first["ordinal"] = 9
    second = json.loads(json.dumps(first, ensure_ascii=False))
    second["sequence"] = 5
    second["ordinal"] = 9
    second["before_state"] = {
        **first["before_state"],
        "trust_level": -1,
    }
    second["after_state"] = {
        **first["after_state"],
        "trust_level": 1,
    }
    duplicate_edge = json.loads(json.dumps(graph["edges"][0], ensure_ascii=False))
    duplicate_edge["relationship_key"] = "duplicate-protagonist-witness"
    duplicate_edge["relationship_types"] = ["ALLY", "SECRET"]
    duplicate_edge["is_core"] = False
    duplicate_edge["ordinal"] = 8
    graph["edges"].append(duplicate_edge)
    second["relationship_key"] = "duplicate-protagonist-witness"
    graph["beats"].append(second)
    graph["edges"][0]["ordinal"] = 7
    graph["core_relationship_keys"] = []
    payload["story_bible"]["narrative_targeting"]["audience_profile"] = "模型改写值"

    normalized = normalize_story_structure_payload(payload, BRIEF)
    structure = StoryStructure.model_validate(normalized)
    beats = structure.relationship_graph.beats

    assert [beat.sequence for beat in beats] == [1, 2]
    assert [beat.ordinal for beat in beats] == [1, 2]
    assert beats[1].before_state == beats[0].after_state
    assert all(beat.relationship_key == "protagonist-witness" for beat in beats)
    assert len(structure.relationship_graph.edges) == 1
    assert structure.relationship_graph.edges[0].ordinal == 1
    assert structure.relationship_graph.edges[0].relationship_types == [
        "RIVAL",
        "SECRET",
        "ALLY",
    ]
    assert structure.relationship_graph.core_relationship_keys == ["protagonist-witness"]
    assert structure.story_bible.narrative_targeting.audience_profile == "25—40岁女性"


class _DelayedSseStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[tuple[float, bytes]]) -> None:
        self.chunks = chunks

    async def __aiter__(self):  # noqa: ANN201
        for delay, chunk in self.chunks:
            await asyncio.sleep(delay)
            yield chunk


class _PulsingSseStream(httpx.AsyncByteStream):
    async def __aiter__(self):  # noqa: ANN201
        for _ in range(100):
            await asyncio.sleep(0.005)
            yield _sse_event("response.output_text.delta", {"delta": "{"})


BRIEF = {
    "project_name": "雨停以后",
    "raw_input": "暴雨停电夜，陌生人被困在便利店，各自藏着同一个秘密。",
    "genre": "urban_suspense",
    "style": "realistic_cinematic",
    "target_duration_sec": 60,
    "aspect_ratio": "9:16",
    "target_platform": "douyin",
    "narrative_protagonist": "ensemble",
    "target_audience": "female_frequency",
    "emotional_rewards": ["identity", "family"],
    "audience_profile": "25—40岁女性",
    "production_format": "live_action",
    "primary_market": "CN",
    "canonical_language": "zh-CN",
    "localization_targets": [],
}


async def test_text_provider_uses_deterministic_contract_without_key() -> None:
    result = await RoutedTextProvider().generate_directions(
        replace(get_settings(), ark_api_key=None), BRIEF
    )
    assert result.provider == "mock"
    assert len(result.payload["directions"]) == 3
    assert {item["direction_key"] for item in result.payload["directions"]} == {
        "emotion",
        "plot",
        "market",
    }
    recommended = sum(
        item["ai_recommendation"]["recommended"] for item in result.payload["directions"]
    )
    assert recommended == 1
    for direction in result.payload["directions"]:
        assert direction["narrative_targeting"] == {
            "narrative_protagonist": "ensemble",
            "target_audience": "female_frequency",
            "emotional_rewards": ["identity", "family"],
            "audience_profile": "25—40岁女性",
            "production_format": "live_action",
        }
        assert len(direction["key_turns"]) >= 3
        assert direction["audience_fit"]
        assert direction["visual_signature"]
        assert direction["selection_tradeoff"]
        assert direction["story_dna"]["stakes"]
        assert direction["story_dna"]["payoff"]
        sequel = direction["sequel_setup"]
        assert all(sequel.values())
        combined_hook = "".join([direction["story_dna"]["ending_hook"], *sequel.values()])
        assert "?" not in combined_hook and "？" not in combined_hook
        assert "评论区" not in combined_hook
        complexity = direction["production_complexity"]
        assert complexity["scene_count"] == len(direction["scenes"])
        assert complexity["estimated_generation"]["video_clips"] >= 1
        assert direction["first_episode_rhythm"]["opening_3s_hook"]
        assert direction["first_episode_rhythm"]["first_payoff"]
        assert direction["first_episode_rhythm"]["ending_action"]


async def test_text_provider_splits_real_directions_into_three_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        item["direction_key"]: item
        for item in deterministic_directions(BRIEF).model_dump(mode="json")["directions"]
    }
    calls: list[str] = []

    async def fake_ark_json(
        _settings: object,
        *,
        prompt: str,
        validator: object,
        transport: object | None = None,
        payload_normalizer=None,  # noqa: ANN001
    ) -> TextGenerationResult:
        del transport
        assert "主角性别只定义叙事视角" in prompt
        assert "女性受众不得自动推导女性主角或大女主" in prompt
        direction_key = next(key for key in source if f"必须为 {key}" in prompt)
        calls.append(direction_key)
        payload = {**source[direction_key]}
        payload["ai_recommendation"] = {
            **payload["ai_recommendation"],
            "recommended": direction_key != "market",
        }
        payload["narrative_targeting"] = {
            **payload["narrative_targeting"],
            "audience_profile": "模型擅自改写的画像",
            "emotional_rewards": ["family", "identity"],
        }
        assert payload_normalizer is not None
        payload = payload_normalizer(payload)
        assert validator is StoryDirection
        return TextGenerationResult(
            payload=payload,
            provider="volcengine-ark",
            model="test-model",
            request_id=f"request-{direction_key}",
            repair_attempts=0,
        )

    monkeypatch.setattr("app.services.text_provider._ark_json", fake_ark_json)
    result = await RoutedTextProvider().generate_directions(
        replace(get_settings(), ark_api_key="test-key"),
        BRIEF,
    )

    assert set(calls) == {"emotion", "plot", "market"}
    assert len(result.payload["directions"]) == 3
    assert result.request_id == "request-emotion,request-plot,request-market"
    recommended = [
        item["direction_key"]
        for item in result.payload["directions"]
        if item["ai_recommendation"]["recommended"]
    ]
    assert recommended == ["market"]
    assert all(
        item["narrative_targeting"]
        == {
            "narrative_protagonist": "ensemble",
            "target_audience": "female_frequency",
            "emotional_rewards": ["identity", "family"],
            "audience_profile": "25—40岁女性",
            "production_format": "live_action",
        }
        for item in result.payload["directions"]
    )


async def test_text_provider_reuses_completed_routes_and_only_generates_missing_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        item["direction_key"]: item
        for item in deterministic_directions(BRIEF).model_dump(mode="json")["directions"]
    }
    existing_results = {
        direction_key: TextGenerationResult(
            payload=source[direction_key],
            provider="volcengine-ark",
            model="test-model",
            request_id=f"request-{direction_key}",
            repair_attempts=0,
        )
        for direction_key in ("emotion", "plot")
    }
    calls: list[str] = []
    completed: list[str] = []

    async def fake_ark_json(
        _settings: object,
        *,
        prompt: str,
        validator: object,
        **_kwargs: object,
    ) -> TextGenerationResult:
        direction_key = next(key for key in source if f"必须为 {key}" in prompt)
        calls.append(direction_key)
        assert validator is StoryDirection
        return TextGenerationResult(
            payload=source[direction_key],
            provider="volcengine-ark",
            model="test-model",
            request_id=f"request-{direction_key}",
            repair_attempts=0,
        )

    async def on_route_complete(
        direction_key: str,
        _result: TextGenerationResult,
    ) -> None:
        completed.append(direction_key)

    monkeypatch.setattr("app.services.text_provider._ark_json", fake_ark_json)
    result = await RoutedTextProvider().generate_directions(
        replace(get_settings(), ark_api_key="test-key"),
        BRIEF,
        existing_results=existing_results,
        on_route_complete=on_route_complete,
    )

    assert calls == ["market"]
    assert completed == ["market"]
    assert [item["direction_key"] for item in result.payload["directions"]] == [
        "emotion",
        "plot",
        "market",
    ]
    assert result.request_id == "request-emotion,request-plot,request-market"


async def test_text_provider_preserves_successful_routes_when_one_route_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        item["direction_key"]: item
        for item in deterministic_directions(BRIEF).model_dump(mode="json")["directions"]
    }
    completed: list[str] = []

    async def fake_ark_json(
        _settings: object,
        *,
        prompt: str,
        **_kwargs: object,
    ) -> TextGenerationResult:
        direction_key = next(key for key in source if f"必须为 {key}" in prompt)
        if direction_key == "market":
            raise TextProviderError(
                "ARK_TEXT_TOTAL_TIMEOUT",
                "市场钩子方向生成超时",
                retryable=True,
                details={"timeout_seconds": 300},
            )
        return TextGenerationResult(
            payload=source[direction_key],
            provider="volcengine-ark",
            model="test-model",
            request_id=f"request-{direction_key}",
            repair_attempts=0,
        )

    async def on_route_complete(
        direction_key: str,
        _result: TextGenerationResult,
    ) -> None:
        completed.append(direction_key)

    monkeypatch.setattr("app.services.text_provider._ark_json", fake_ark_json)
    with pytest.raises(TextProviderError) as caught:
        await RoutedTextProvider().generate_directions(
            replace(get_settings(), ark_api_key="test-key"),
            BRIEF,
            on_route_complete=on_route_complete,
        )

    assert set(completed) == {"emotion", "plot"}
    assert caught.value.code == "ARK_TEXT_PARTIAL_FAILURE"
    assert caught.value.retryable is True
    assert caught.value.details["completed_routes"] == ["emotion", "plot"]
    assert caught.value.details["failed_routes"] == ["market"]
    assert caught.value.details["failed_parts"] == ["market"]
    assert caught.value.details["route_errors"]["market"]["code"] == (
        "ARK_TEXT_TOTAL_TIMEOUT"
    )


def test_first_topic_slate_mix_matches_production_format_strategy() -> None:
    assert TOPIC_SLATE_MIX == {
        "live_action": {
            "female_frequency": 50,
            "general": 30,
            "male_frequency": 20,
        },
        "ai_comic": {
            "male_frequency": 50,
            "general": 30,
            "female_frequency": 20,
        },
        "high_concept_fantasy": {
            "male_frequency": 50,
            "general": 30,
            "female_frequency": 20,
        },
    }


@pytest.mark.parametrize(
    ("narrative_protagonist", "target_audience", "expected_gender"),
    [
        ("male", "female_frequency", "male"),
        ("female", "male_frequency", "female"),
    ],
)
def test_protagonist_and_audience_remain_independent(
    narrative_protagonist: str,
    target_audience: str,
    expected_gender: str,
) -> None:
    brief = {
        **BRIEF,
        "narrative_protagonist": narrative_protagonist,
        "target_audience": target_audience,
        "emotional_rewards": ["family", "public_mission"],
    }
    direction = deterministic_directions(brief).model_dump(mode="json")["directions"][0]
    package = deterministic_story_package(brief, direction).model_dump(mode="json")

    assert direction["narrative_targeting"]["narrative_protagonist"] == narrative_protagonist
    assert direction["narrative_targeting"]["target_audience"] == target_audience
    assert package["story_bible"]["characters"][0]["gender"] == expected_gender
    generated = json.dumps({"direction": direction, "package": package}, ensure_ascii=False)
    assert all(term not in generated for term in ("战神", "赘婿", "后宫", "大女主"))


def test_unrequested_stereotype_is_rejected_but_explicit_request_is_allowed() -> None:
    male_brief = {
        **BRIEF,
        "narrative_protagonist": "male",
        "target_audience": "general",
    }
    with pytest.raises(ValueError, match="未经用户要求"):
        reject_unrequested_stereotypes({"logline": "退役战神回归都市"}, male_brief)

    reject_unrequested_stereotypes(
        {"logline": "退役战神回归都市"},
        {**male_brief, "raw_input": "退役战神回归都市寻找失踪的朋友"},
    )


def test_story_direction_rejects_audience_question_as_sequel_hook() -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    direction["story_dna"]["ending_hook"] = "如果你是主角，你会怎么选？"

    with pytest.raises(ValidationError, match="具体剧情铺垫"):
        StoryDirection.model_validate(direction)


def test_direction_batch_requires_exactly_one_ai_recommendation() -> None:
    payload = deterministic_directions(BRIEF).model_dump(mode="json")
    for direction in payload["directions"]:
        direction["ai_recommendation"]["recommended"] = False

    with pytest.raises(ValidationError, match="只能推荐一个"):
        type(deterministic_directions(BRIEF)).model_validate(payload)


def test_story_direction_returns_itemized_brief_compliance() -> None:
    brief = {
        **BRIEF,
        "content_requirements": ["前三秒建立冲突"],
        "content_avoidances": ["不得出现未授权品牌"],
    }
    direction = deterministic_directions(brief).model_dump(mode="json")["directions"][0]
    compliance = direction["brief_compliance"]

    assert compliance["status"] == "PARTIAL"
    assert [item["item"] for item in compliance["items"]] == [
        "前三秒建立冲突",
        "不得出现未授权品牌",
    ]
    assert {item["category"] for item in compliance["items"]} == {
        "REQUIREMENT",
        "AVOIDANCE",
    }
    assert all(item["evidence"] for item in compliance["items"])


def test_story_package_persists_short_drama_generation_contract() -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    package = deterministic_story_package(BRIEF, direction).model_dump(mode="json")
    script = package["scripts"][0]
    engine = script["short_drama_engine"]

    assert engine["formula_version"] == "short-drama-v1"
    assert engine["protagonist_desire"]
    assert engine["payoff_strategy"]
    assert len(engine["reversal_chain"]) >= 2
    assert engine["stage_closure"]
    assert engine["continuation_hook"] == direction["story_dna"]["ending_hook"]
    beat_types = [item["beat_type"] for item in engine["beats"]]
    assert beat_types[0] == "HOOK"
    assert beat_types.count("REVERSAL") >= 2
    assert "PAYOFF" in beat_types
    assert "CLOSURE" in beat_types
    assert beat_types[-1] == "CONTINUATION_HOOK"
    breakout = script["breakout_engine"]
    assert breakout["formula_version"] == "breakout-drama-v1"
    assert breakout["formula"] == (
        "弱势外壳 × 顶级内核 × 持续误判 × 分段认证 × 关系重排 × 情感秩序重建 × 可续作单元"
    )
    assert breakout["vulnerable_shell"]
    assert breakout["elite_core"]
    assert len(breakout["misjudgment_chain"]) >= 2
    assert len(breakout["authentication_ladder"]) >= 2
    assert breakout["relationship_reorders"]
    assert breakout["emotional_order_rebuild"]["old_order"]
    assert breakout["emotional_order_rebuild"]["new_order"]
    assert breakout["sequel_unit"]["current_unit_closure"] == engine["stage_closure"]
    assert breakout["sequel_unit"]["next_unit_trigger"] == engine["continuation_hook"]
    assert (
        package["critic"]["checks"].items()
        >= {
            "protagonist_desire": "PASS",
            "pace_density": "PASS",
            "emotional_payoff": "PASS",
            "progressive_reversals": "PASS",
            "stage_closure": "PASS",
            "continuation_hook": "PASS",
            "vulnerable_shell": "PASS",
            "elite_core": "PASS",
            "sustained_misjudgment": "PASS",
            "staged_authentication": "PASS",
            "relationship_reorder": "PASS",
            "emotional_order_rebuild": "PASS",
            "sequel_unit": "PASS",
        }.items()
    )


def test_story_package_rejects_incomplete_breakout_chain() -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    package = deterministic_story_package(BRIEF, direction).model_dump(mode="json")
    breakout = package["scripts"][0]["breakout_engine"]
    breakout["misjudgment_chain"] = breakout["misjudgment_chain"][:1]

    with pytest.raises(ValidationError):
        StoryPackage.model_validate(package)


def test_story_structure_separates_story_bible_and_relationship_graph() -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    structure = deterministic_story_structure(BRIEF, direction)

    bible = structure.story_bible.model_dump(mode="json")
    graph = structure.relationship_graph.model_dump(mode="json")
    assert "relationships" not in bible
    assert {item["key"] for item in bible["characters"]} == {"protagonist", "witness"}
    assert graph["core_relationship_keys"] == ["protagonist-witness"]
    assert graph["beats"][0]["trigger_ref"] == "authentication:2"


async def test_script_package_strongly_references_approved_relationship_context() -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    structure = deterministic_story_structure(BRIEF, direction)
    relationship_graph = structure.relationship_graph.model_dump(mode="json")
    relationship_graph["graph_version_id"] = "graph-v1"
    relationship_graph["content_hash"] = "hash-v1"
    relationship_graph["beats"][0]["relationship_beat_id"] = "beat-v1"

    result = await RoutedTextProvider().generate_script_package(
        replace(get_settings(), ark_api_key=None),
        BRIEF,
        direction,
        structure.story_bible.model_dump(mode="json"),
        relationship_graph,
    )
    package = ScriptPackageOutput.model_validate(result.payload)
    reorder = package.scripts[0].breakout_engine.relationship_reorders[0]
    assert reorder.source_character_key == "protagonist"
    assert reorder.target_character_key == "witness"
    assert reorder.relationship_beat_id == "beat-v1"
    assert reorder.before_state is not None and reorder.before_state.trust_level == -2
    assert reorder.after_state is not None and reorder.after_state.trust_level == 0

    invalid = package.model_copy(deep=True)
    invalid.scripts[0].breakout_engine.relationship_reorders[0].relationship_beat_id = "not-real"
    with pytest.raises(ValueError, match="变化节点"):
        validate_script_package_relationship_contract(invalid, relationship_graph)

    leaked = package.model_copy(deep=True)
    leaked.scripts[0].scenes[0].lines[0].text = relationship_graph["edges"][0]["secret"]
    with pytest.raises(ValueError, match="秘密在设定揭示场景之前泄露"):
        validate_script_package_relationship_contract(leaked, relationship_graph)


def test_split_story_package_assembly_repairs_only_mechanical_invariants() -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    package = deterministic_story_package(BRIEF, direction)
    foundation = StoryFoundation(story_bible=package.story_bible, outlines=package.outlines)
    source_script = package.scripts[0]
    draft = EpisodeScriptDraft(
        episode_ordinal=1,
        title=source_script.title,
        canonical_language=source_script.canonical_language,
        scenes=source_script.scenes,
    )
    review = NarrativeReview(
        short_drama_engine=source_script.short_drama_engine,
        breakout_engine=source_script.breakout_engine,
        critic=package.critic,
    ).model_dump(mode="json")
    for beat in review["short_drama_engine"]["beats"]:
        beat["at_ms"] = 90_000
        beat["scene_ordinal"] = 8
    review["breakout_engine"]["sequel_unit"]["current_unit_closure"] = "模型重复生成的旧闭环"
    review["breakout_engine"]["sequel_unit"]["next_unit_trigger"] = "模型重复生成的旧钩子"
    for item in review["breakout_engine"]["authentication_ladder"]:
        item["scene_ordinal"] = 8

    result = assemble_story_package(
        TextGenerationResult(foundation.model_dump(mode="json"), "test", "test", "1", 1),
        TextGenerationResult(draft.model_dump(mode="json"), "test", "test", "2", 1),
        TextGenerationResult(review, "test", "test", "3", 1),
    )

    script = result.payload["scripts"][0]
    beats = script["short_drama_engine"]["beats"]
    assert script["estimated_duration_ms"] == sum(
        scene["duration_ms"] for scene in script["scenes"]
    )
    assert [beat["sequence"] for beat in beats] == list(range(1, len(beats) + 1))
    assert [beat["at_ms"] for beat in beats] == sorted(beat["at_ms"] for beat in beats)
    assert all(beat["at_ms"] < script["estimated_duration_ms"] for beat in beats)
    assert all(beat["scene_ordinal"] <= len(script["scenes"]) for beat in beats)
    assert (
        script["breakout_engine"]["sequel_unit"]["current_unit_closure"]
        == script["short_drama_engine"]["stage_closure"]
    )
    assert (
        script["breakout_engine"]["sequel_unit"]["next_unit_trigger"]
        == script["short_drama_engine"]["continuation_hook"]
    )
    assert result.repair_attempts == 3


async def test_story_package_provider_uses_three_scoped_generation_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    package = deterministic_story_package(BRIEF, direction)
    validators: list[str] = []

    async def fake_ark_json(  # noqa: ANN001
        _settings,
        *,
        prompt,
        validator,
        transport=None,
        payload_normalizer=None,
        thinking_type=None,
    ):
        del payload_normalizer, prompt, thinking_type, transport
        validators.append(validator.__name__)
        if validator is StoryFoundation:
            payload = StoryFoundation(
                story_bible=package.story_bible, outlines=package.outlines
            ).model_dump(mode="json")
        elif validator is EpisodeScriptDraft:
            source = package.scripts[0]
            payload = EpisodeScriptDraft(
                episode_ordinal=1,
                title=source.title,
                canonical_language=source.canonical_language,
                scenes=source.scenes,
            ).model_dump(mode="json")
        else:
            source = package.scripts[0]
            payload = NarrativeReview(
                short_drama_engine=source.short_drama_engine,
                breakout_engine=source.breakout_engine,
                critic=package.critic,
            ).model_dump(mode="json")
        return TextGenerationResult(payload, "volcengine-ark", "test-model", "request", 0)

    from app.services import text_provider as module

    monkeypatch.setattr(module, "_ark_json", fake_ark_json)
    result = await RoutedTextProvider().generate_story_package(
        replace(get_settings(), ark_api_key="test-key"), BRIEF, direction
    )

    assert validators == ["StoryFoundation", "EpisodeScriptDraft", "NarrativeReview"]
    assert StoryPackage.model_validate(result.payload)


def test_90_second_brief_preserves_direction_and_script_duration() -> None:
    brief = {**BRIEF, "target_duration_sec": 90}
    direction = deterministic_directions(brief).model_dump(mode="json")["directions"][0]
    package = deterministic_story_package(brief, direction).model_dump(mode="json")

    assert direction["total_duration_sec"] == 90
    assert sum(scene["duration_sec"] for scene in direction["scenes"]) == 90
    assert package["outlines"][0]["target_duration_sec"] == 90
    assert package["scripts"][0]["estimated_duration_ms"] == 90_000


async def test_ark_text_provider_repairs_invalid_json_once() -> None:
    valid = {
        item["direction_key"]: item
        for item in deterministic_directions(BRIEF).model_dump(mode="json")["directions"]
    }
    calls = {key: 0 for key in valid}

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(request.content)
        assert request_payload["stream"] is True
        prompt = request_payload["input"]
        direction_key = next(key for key in valid if f"必须为 {key}" in prompt)
        calls[direction_key] += 1
        attempt = calls[direction_key]
        output = (
            "not-json" if attempt == 1 else json.dumps(valid[direction_key], ensure_ascii=False)
        )
        return httpx.Response(
            200,
            content=(
                _sse_event("response.output_text.delta", {"delta": output})
                + _sse_event("response.completed", {"response": {}})
                + b"data: [DONE]\n\n"
            ),
            headers={"x-request-id": f"request-{direction_key}-{attempt}"},
        )

    provider = RoutedTextProvider()
    from app.services import text_provider as module

    original = module.httpx.AsyncClient

    def client_factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    module.httpx.AsyncClient = client_factory
    try:
        result = await provider.generate_directions(
            replace(get_settings(), ark_api_key="test-key"), BRIEF
        )
    finally:
        module.httpx.AsyncClient = original
    assert calls == {"emotion": 2, "plot": 2, "market": 2}
    assert result.provider == "volcengine-ark"
    assert result.repair_attempts == 3
    assert result.request_id == ("request-emotion-2,request-plot-2,request-market-2")


async def test_story_structure_repairs_missing_reveal_from_semantic_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    valid = deterministic_story_structure(BRIEF, direction).model_dump(mode="json")
    missing_reveal = json.loads(json.dumps(valid, ensure_ascii=False))
    missing_reveal["relationship_graph"]["beats"] = []
    regenerated = json.loads(json.dumps(valid, ensure_ascii=False))
    regenerated["story_bible"]["world"] = "模型重写了不应变更的世界设定"
    regenerated["relationship_graph"]["edges"][0][
        "relationship_key"
    ] = "renamed-protagonist-witness"
    regenerated["relationship_graph"]["core_relationship_keys"] = [
        "renamed-protagonist-witness"
    ]
    for beat in regenerated["relationship_graph"]["beats"]:
        beat["relationship_key"] = "renamed-protagonist-witness"
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        prompts.append(str(json.loads(request.content)["input"]))
        output = missing_reveal if len(prompts) == 1 else regenerated
        return httpx.Response(
            200,
            content=(
                _sse_event(
                    "response.output_text.delta",
                    {"delta": json.dumps(output, ensure_ascii=False)},
                )
                + _sse_event("response.completed", {"response": {}})
                + b"data: [DONE]\n\n"
            ),
            headers={"x-request-id": f"relationship-request-{len(prompts)}"},
        )

    from app.services import text_provider as module

    original = module.httpx.AsyncClient

    def client_factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client_factory)
    result = await RoutedTextProvider().generate_story_structure(
        replace(get_settings(), ark_api_key="test-key"),
        BRIEF,
        direction,
    )

    assert len(prompts) == 2
    assert "输出前逐条自检 relationship_graph.edges" in prompts[0]
    assert "relationship_key 完全相同" in prompts[0]
    assert "relationship_key=protagonist-witness" in prompts[1]
    assert "HIDDEN_RELATIONSHIP_WITHOUT_REVEAL" in prompts[1]
    assert "只返回修复后的完整 JSON" in prompts[1]
    assert "上一轮完整 JSON" in prompts[1]
    assert '"beats":[]' in prompts[1]
    assert result.repair_attempts == 1
    assert result.request_id == "relationship-request-2"
    assert missing_reveal["relationship_graph"]["beats"] == []
    assert result.payload["relationship_graph"]["beats"] == valid["relationship_graph"]["beats"]
    assert result.payload["relationship_graph"]["edges"] == valid["relationship_graph"]["edges"]
    assert result.payload["story_bible"]["world"] == valid["story_bible"]["world"]


async def test_story_structure_keeps_semantic_error_after_later_schema_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    first = deterministic_story_structure(BRIEF, direction).model_dump(mode="json")
    first["relationship_graph"]["beats"] = []
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        output = first if calls == 1 else {}
        return httpx.Response(
            200,
            content=(
                _sse_event(
                    "response.output_text.delta",
                    {"delta": json.dumps(output, ensure_ascii=False)},
                )
                + _sse_event("response.completed", {"response": {}})
                + b"data: [DONE]\n\n"
            ),
        )

    from app.services import text_provider as module

    original = module.httpx.AsyncClient

    def client_factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client_factory)
    with pytest.raises(TextProviderError) as caught:
        await RoutedTextProvider().generate_story_structure(
            replace(get_settings(), ark_api_key="test-key"),
            BRIEF,
            direction,
        )

    assert calls == 3
    assert caught.value.code == "RELATIONSHIP_GRAPH_SEMANTIC_INVALID"
    assert [item["error_type"] for item in caught.value.details["attempts"]] == [
        "semantic_validation_error",
        "validation_error",
        "validation_error",
    ]


async def test_story_structure_semantic_failure_keeps_true_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direction = deterministic_directions(BRIEF).model_dump(mode="json")["directions"][0]
    invalid = deterministic_story_structure(BRIEF, direction).model_dump(mode="json")
    invalid["relationship_graph"]["beats"] = []
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=(
                _sse_event(
                    "response.output_text.delta",
                    {"delta": json.dumps(invalid, ensure_ascii=False)},
                )
                + _sse_event("response.completed", {"response": {}})
                + b"data: [DONE]\n\n"
            ),
            headers={"x-request-id": f"semantic-failure-{calls}"},
        )

    from app.services import text_provider as module

    original = module.httpx.AsyncClient

    def client_factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client_factory)
    with pytest.raises(TextProviderError) as caught:
        await RoutedTextProvider().generate_story_structure(
            replace(get_settings(), ark_api_key="test-key"),
            BRIEF,
            direction,
        )

    assert calls == 3
    assert caught.value.code == "RELATIONSHIP_GRAPH_SEMANTIC_INVALID"
    assert caught.value.code != "ARK_TEXT_NETWORK_ERROR"
    assert caught.value.retryable is False
    assert caught.value.details["hidden_relationship_count"] == 1
    assert caught.value.details["hidden_relationship_keys"] == ["protagonist-witness"]
    assert len(caught.value.details["attempts"]) == 3


async def test_ark_text_provider_rejects_three_invalid_structures() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                _sse_event("response.output_text.delta", {"delta": "{}"})
                + _sse_event("response.completed", {"response": {}})
                + b"data: [DONE]\n\n"
            ),
        )

    from app.services import text_provider as module

    original = module.httpx.AsyncClient

    def client_factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    module.httpx.AsyncClient = client_factory
    try:
        with pytest.raises(TextProviderError) as caught:
            await RoutedTextProvider().generate_directions(
                replace(get_settings(), ark_api_key="test-key"), BRIEF
            )
    finally:
        module.httpx.AsyncClient = original
    assert caught.value.code == "ARK_TEXT_SCHEMA_INVALID"
    assert caught.value.retryable is True
    assert caught.value.details["validator"] == "StoryDirection"
    assert len(caught.value.details["attempts"]) == 3
    assert all(item["validation_error"] for item in caught.value.details["attempts"])


async def test_ark_json_reports_each_validation_failure_before_terminal_error() -> None:
    attempts: list[tuple[int, dict[str, object]]] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                _sse_event("response.output_text.delta", {"delta": "{}"})
                + _sse_event("response.completed", {"response": {}})
                + b"data: [DONE]\n\n"
            ),
        )

    async def record_failure(attempt: int, diagnostic: dict[str, object]) -> None:
        attempts.append((attempt, diagnostic))

    with pytest.raises(TextProviderError):
        await _ark_json(
            replace(get_settings(), ark_api_key="test-key"),
            prompt="return a story direction",
            validator=StoryDirection,
            transport=httpx.MockTransport(handler),
            on_validation_failure=record_failure,
        )

    assert [attempt for attempt, _ in attempts] == [1, 2, 3]
    assert all(item["error_type"] == "validation_error" for _, item in attempts)


async def test_ark_stream_distinguishes_first_byte_timeout() -> None:
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://ark.example/responses"),
        stream=_DelayedSseStream(
            [(0.05, _sse_event("response.output_text.delta", {"delta": "{}"}))]
        ),
    )
    try:
        with pytest.raises(TextProviderError) as caught:
            await _ark_stream_output(
                response,
                request_id="request-first-byte",
                total_timeout_seconds=0.01,
            )
    finally:
        await response.aclose()

    assert caught.value.code == "ARK_TEXT_FIRST_BYTE_TIMEOUT"
    assert caught.value.details["request_id"] == "request-first-byte"


async def test_ark_stream_distinguishes_idle_timeout() -> None:
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://ark.example/responses"),
        stream=_DelayedSseStream(
            [
                (0, _sse_event("response.output_text.delta", {"delta": "{"})),
                (0.05, _sse_event("response.output_text.delta", {"delta": "}"})),
            ]
        ),
    )
    try:
        with pytest.raises(TextProviderError) as caught:
            await _ark_stream_output(
                response,
                request_id="request-idle",
                total_timeout_seconds=0.01,
            )
    finally:
        await response.aclose()

    assert caught.value.code == "ARK_TEXT_STREAM_IDLE_TIMEOUT"
    assert caught.value.details["request_id"] == "request-idle"


async def test_ark_stream_distinguishes_total_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_PulsingSseStream(),
            headers={"x-request-id": "request-total"},
        )

    with pytest.raises(TextProviderError) as caught:
        await _ark_json(
            replace(
                get_settings(),
                ark_api_key="test-key",
                ark_request_timeout_seconds=0.03,
            ),
            prompt="return a story direction",
            validator=StoryDirection,
            transport=httpx.MockTransport(handler),
        )

    assert caught.value.code == "ARK_TEXT_TOTAL_TIMEOUT"
    assert caught.value.details["request_id"] == "request-total"
