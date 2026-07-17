import pytest
from pydantic import ValidationError

from app.schemas import RelationshipGraphPayload
from app.services.relationship_graphs import (
    adapt_legacy_relationships,
    build_legacy_relationship_graph,
    canonical_character_pair_key,
    relationship_graph_has_blockers,
    validate_relationship_graph,
)

STORY_BIBLE = {
    "characters": [
        {
            "key": "protagonist",
            "name": "林岚",
            "role": "PROTAGONIST",
        },
        {
            "key": "witness",
            "name": "周启",
            "role": "SUPPORTING",
        },
    ],
    "relationships": ["林岚与周启共享一段被删除的过去"],
}


def relationship_state(
    *,
    surface: str,
    truth: str,
    trust: int,
    power: int,
    conflict: int,
) -> dict[str, object]:
    return {
        "surface_relationship": surface,
        "true_relationship": truth,
        "trust_level": trust,
        "emotional_temperature": trust,
        "power_balance": power,
        "conflict_intensity": conflict,
    }


def valid_relationship_graph() -> dict[str, object]:
    return {
        "schema_version": "relationship-graph-v1",
        "edges": [
            {
                "relationship_key": "protagonist-witness",
                "source_character_key": "protagonist",
                "target_character_key": "witness",
                "directionality": "BIDIRECTIONAL",
                "relationship_types": ["RIVAL", "SECRET"],
                "surface_relationship": "互相审视的嫌疑人与证人",
                "true_relationship": "共享旧案秘密的对立知情者",
                "source_view": {
                    "perceived_relationship": "可能隐瞒真相的证人",
                    "belief": "周启掌握照片原件但没有说出全部事实",
                },
                "target_view": {
                    "perceived_relationship": "试图逃避过去的嫌疑人",
                    "belief": "林岚仍在控制证据和现场叙事",
                },
                "trust_level": -2,
                "emotional_temperature": -1,
                "power_balance": 1,
                "conflict_intensity": 3,
                "story_function": "通过旧案秘密制造误判，并在认证后完成权力重排",
                "secret": "两人共同认识照片中被删除的人",
                "is_core": True,
                "locked": False,
                "ordinal": 1,
            }
        ],
        "beats": [
            {
                "relationship_key": "protagonist-witness",
                "episode_ordinal": 1,
                "sequence": 1,
                "scene_ordinal": 2,
                "trigger_type": "AUTHENTICATION",
                "trigger_ref": "authentication:2",
                "before_state": relationship_state(
                    surface="互相审视的嫌疑人与证人",
                    truth="共享旧案秘密的对立知情者",
                    trust=-2,
                    power=1,
                    conflict=3,
                ),
                "after_state": relationship_state(
                    surface="受约束的临时合作方",
                    truth="共同面对旧案的有条件盟友",
                    trust=0,
                    power=0,
                    conflict=1,
                ),
                "evidence": "周启交出照片原件，验证林岚的证据链",
                "emotional_consequence": "羞耻与防御转为有限信任",
                "audience_visibility": "REVEALED",
                "ordinal": 1,
            }
        ],
        "core_relationship_keys": ["protagonist-witness"],
        "generation_notes": [],
    }


def test_relationship_graph_accepts_valid_contract() -> None:
    payload = RelationshipGraphPayload.model_validate(valid_relationship_graph())
    issues = validate_relationship_graph(payload, STORY_BIBLE)

    assert not relationship_graph_has_blockers(issues)
    assert canonical_character_pair_key("witness", "protagonist") == ("protagonist|witness")


def test_relationship_graph_rejects_reverse_duplicate_pair() -> None:
    graph = valid_relationship_graph()
    duplicate = {
        **graph["edges"][0],  # type: ignore[index]
        "relationship_key": "witness-protagonist",
        "source_character_key": "witness",
        "target_character_key": "protagonist",
        "ordinal": 2,
        "is_core": False,
    }
    graph["edges"] = [graph["edges"][0], duplicate]  # type: ignore[index]

    with pytest.raises(ValidationError, match="同一角色对"):
        RelationshipGraphPayload.model_validate(graph)


def test_relationship_graph_rejects_self_relationship() -> None:
    graph = valid_relationship_graph()
    graph["edges"][0]["target_character_key"] = "protagonist"  # type: ignore[index]

    with pytest.raises(ValidationError, match="角色与自身"):
        RelationshipGraphPayload.model_validate(graph)


def test_relationship_graph_rejects_unknown_beat_relationship() -> None:
    graph = valid_relationship_graph()
    graph["beats"][0]["relationship_key"] = "not-real"  # type: ignore[index]

    with pytest.raises(ValidationError, match="不存在的 relationship_key"):
        RelationshipGraphPayload.model_validate(graph)


def test_relationship_graph_rejects_discontinuous_beat_states() -> None:
    graph = valid_relationship_graph()
    second = {
        **graph["beats"][0],  # type: ignore[index]
        "sequence": 2,
        "ordinal": 2,
        "trigger_type": "CHOICE",
        "trigger_ref": None,
        "before_state": relationship_state(
            surface="没有承接上一变化的状态",
            truth="共享旧案秘密的对立知情者",
            trust=-2,
            power=1,
            conflict=3,
        ),
        "after_state": relationship_state(
            surface="公开结盟",
            truth="共同面对旧案的盟友",
            trust=1,
            power=0,
            conflict=0,
        ),
    }
    graph["beats"].append(second)  # type: ignore[union-attr]

    with pytest.raises(ValidationError, match="前后状态必须连续"):
        RelationshipGraphPayload.model_validate(graph)


def test_relationship_graph_validation_reports_missing_character_and_reveal() -> None:
    graph = valid_relationship_graph()
    edge = graph["edges"][0]  # type: ignore[index]
    edge["target_character_key"] = "ghost"
    graph["beats"][0]["trigger_type"] = "STORY_EVENT"  # type: ignore[index]
    graph["beats"][0]["trigger_ref"] = None  # type: ignore[index]
    payload = RelationshipGraphPayload.model_validate(graph)

    issues = validate_relationship_graph(payload, STORY_BIBLE)
    codes = {item.code for item in issues}

    assert "INVALID_CHARACTER_REFERENCE" in codes
    assert "CORE_CHARACTER_ISOLATED" in codes
    assert "HIDDEN_RELATIONSHIP_WITHOUT_REVEAL" in codes
    assert relationship_graph_has_blockers(issues)


def test_legacy_relationship_adapter_maps_only_explicit_two_character_text() -> None:
    result = adapt_legacy_relationships(STORY_BIBLE)

    assert result.status == "MAPPED"
    assert result.can_create_draft is True
    assert result.summaries[0].source_character_key == "protagonist"
    assert result.summaries[0].target_character_key == "witness"
    assert result.summaries[0].raw_text == STORY_BIBLE["relationships"][0]

    draft = build_legacy_relationship_graph(STORY_BIBLE)
    assert draft is not None
    assert draft.edges[0].surface_relationship == STORY_BIBLE["relationships"][0]
    assert draft.edges[0].true_relationship == STORY_BIBLE["relationships"][0]
    assert draft.edges[0].relationship_types == ["OTHER"]
    assert draft.core_relationship_keys == []
    assert draft.beats == []


def test_legacy_relationship_adapter_does_not_invent_ambiguous_mapping() -> None:
    story_bible = {
        **STORY_BIBLE,
        "relationships": [
            STORY_BIBLE["relationships"][0],
            "有人仍在隐藏另一段过去",
        ],
    }

    result = adapt_legacy_relationships(story_bible)

    assert result.status == "PARTIAL"
    assert result.can_create_draft is False
    assert result.summaries[1].status == "UNMAPPED"
    assert build_legacy_relationship_graph(story_bible) is None


def test_legacy_relationship_adapter_rejects_duplicate_character_names() -> None:
    story_bible = {
        "characters": [
            {"key": "older", "name": "小林", "role": "PROTAGONIST"},
            {"key": "younger", "name": "小林", "role": "SUPPORTING"},
            {"key": "witness", "name": "周启", "role": "SUPPORTING"},
        ],
        "relationships": ["小林与周启共享一个秘密"],
    }

    result = adapt_legacy_relationships(story_bible)

    assert result.status == "UNMAPPED"
    assert result.can_create_draft is False
    assert result.summaries[0].reason == "必须恰好识别出两个不同角色才能安全转换"


def test_legacy_draft_requires_human_completion_before_approval() -> None:
    draft = build_legacy_relationship_graph(STORY_BIBLE)
    assert draft is not None

    issues = validate_relationship_graph(draft, STORY_BIBLE)
    codes = {item.code for item in issues}

    assert "MISSING_CORE_RELATIONSHIP" in codes
    assert "MISSING_PRIMARY_CONFLICT" in codes
    assert "MISSING_RELATIONSHIP_BEAT" in codes
    assert relationship_graph_has_blockers(issues)
