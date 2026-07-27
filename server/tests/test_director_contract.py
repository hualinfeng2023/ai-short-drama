import pytest
from pydantic import ValidationError

from app.domain.director import (
    DirectorReviewOutput,
    director_change_target_issues,
)

SCENE_ID = "10000000-0000-4000-8000-000000000001"
LINE_ID = "10000000-0000-4000-8000-000000000002"
OTHER_ID = "10000000-0000-4000-8000-000000000003"


def review_payload(*, proposed_change: dict[str, object]) -> dict[str, object]:
    return {
        "issue_type": "PACING",
        "observation": "当前场景节奏需要调整。",
        "rationale": "先修正文本节奏，不触发媒体生成。",
        "options": [
            {
                "option_id": "primary",
                "title": "收紧节奏",
                "rationale": "减少解释性表达。",
                "proposed_change": proposed_change,
                "estimated_time_seconds": 1,
                "estimated_cost_usd": 0,
            },
            {
                "option_id": "alternative",
                "title": "强化停顿",
                "rationale": "保留文本，只调整停顿。",
                "proposed_change": {
                    "scope": "LINE",
                    "entity_id": LINE_ID,
                    "changes": {"pause_after_ms": 100},
                    "before": {"pause_after_ms": 300},
                },
                "estimated_time_seconds": 1,
                "estimated_cost_usd": 0,
            },
        ],
        "recommended_option_id": "primary",
        "confidence": 0.8,
        "validation_plan": ["比较修改前后节奏"],
    }


def test_director_contract_rejects_non_whitelisted_line_field() -> None:
    with pytest.raises(ValidationError) as caught:
        DirectorReviewOutput.model_validate(
            review_payload(
                proposed_change={
                    "scope": "LINE",
                    "entity_id": LINE_ID,
                    "changes": {"estimated_duration_ms": 1600},
                    "before": {"estimated_duration_ms": 3000},
                }
            )
        )

    assert "estimated_duration_ms" in str(caught.value)
    assert "Extra inputs are not permitted" in str(caught.value)


def test_director_contract_rejects_scene_fields_on_line_changes() -> None:
    with pytest.raises(ValidationError) as caught:
        DirectorReviewOutput.model_validate(
            review_payload(
                proposed_change={
                    "scope": "LINE",
                    "entity_id": LINE_ID,
                    "changes": {"purpose": "公开核心分歧"},
                    "before": {"purpose": "让两人的分歧公开化"},
                }
            )
        )

    assert "purpose" in str(caught.value)


def test_director_contract_requires_matching_before_fields() -> None:
    with pytest.raises(ValidationError) as caught:
        DirectorReviewOutput.model_validate(
            review_payload(
                proposed_change={
                    "scope": "LINE",
                    "entity_id": LINE_ID,
                    "changes": {"text": "现在离开。", "pause_after_ms": 100},
                    "before": {"text": "其实我觉得，你现在必须离开这里。"},
                }
            )
        )

    assert "before 字段必须与 changes 完全对应" in str(caught.value)


def test_director_contract_enforces_value_ranges() -> None:
    with pytest.raises(ValidationError) as caught:
        DirectorReviewOutput.model_validate(
            review_payload(
                proposed_change={
                    "scope": "LINE",
                    "entity_id": LINE_ID,
                    "changes": {"speech_rate": 1.8},
                    "before": {"speech_rate": 1.0},
                }
            )
        )

    assert "less than or equal to 1.4" in str(caught.value)


def test_director_contract_enforces_scope_target_lineage() -> None:
    review = DirectorReviewOutput.model_validate(
        review_payload(
            proposed_change={
                "scope": "SCENE",
                "entity_id": OTHER_ID,
                "changes": {"purpose": "公开核心分歧"},
                "before": {"purpose": "让两人的分歧公开化"},
            }
        )
    )

    assert director_change_target_issues(
        review,
        scene_id=SCENE_ID,
        line_ids={LINE_ID},
    ) == [
        {
            "code": "SCENE_TARGET_INVALID",
            "option_id": "primary",
            "scope": "SCENE",
            "entity_id": OTHER_ID,
        }
    ]


def test_director_contract_serializes_only_explicit_patch_fields() -> None:
    review = DirectorReviewOutput.model_validate(
        review_payload(
            proposed_change={
                "scope": "LINE",
                "entity_id": LINE_ID,
                "changes": {"text": "现在离开。"},
                "before": {"text": "其实我觉得，你现在必须离开这里。"},
            }
        )
    )

    change = review.options[0].proposed_change.model_dump(
        mode="json",
        exclude_none=True,
    )
    assert change["changes"] == {"text": "现在离开。"}
    assert change["before"] == {"text": "其实我觉得，你现在必须离开这里。"}
    assert "estimated_duration_ms" not in str(DirectorReviewOutput.model_json_schema())
