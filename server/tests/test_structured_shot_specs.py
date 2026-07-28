import json
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.domain.shot_spec import ShotSpec, StoryboardShotPlan
from app.services.prompt_compiler import PromptCompiler
from app.services.shot_specs import build_structured_shot_spec
from app.services.shot_validator import ShotValidator
from app.services.storyboard_agent import generate_storyboard_shot_specs
from app.services.text_provider import TextProviderError, _ark_json


def _draft(*, code: str = "S01-01", duration_sec: float = 3) -> ShotSpec:
    return build_structured_shot_spec(
        duration_sec=duration_sec,
        narrative_goal="人物发现关键线索",
        description="林岚在柜台前发现一张被雨水打湿的收据",
        action="林岚拿起收据并看向门外",
        environment="停电后的便利店，窗外暴雨",
        location="便利店",
        time_of_day="夜",
        shot_size="MCU",
        camera_movement="DOLLY_IN",
        character_ids=["character-linlan"],
        character_names=["林岚"],
        prop_version_ids=["prop-receipt-v1"],
        prop_names=["收据"],
        location_version_id="location-store-v1",
        dialogue="这张收据不是今晚的。",
        delivery="DIALOGUE",
        project_style="写实悬疑电影风格",
        aspect_ratio="9:16",
        source_scene_ordinal=1,
        source_script_scene_id="script-scene-1",
        source_script_line_ids=[f"line-{code}"],
        code=code,
        title="发现收据",
        reference_asset_ids=["asset-linlan", "asset-receipt"],
    )


def _sse_event(event_type: str, payload: dict[str, object]) -> bytes:
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"
    ).encode()


def test_shot_spec_rejects_missing_fields_and_unknown_prompt_fields() -> None:
    payload = _draft().model_dump(mode="json")
    payload.pop("lighting")
    with pytest.raises(ValidationError):
        ShotSpec.model_validate(payload)

    payload = _draft().model_dump(mode="json")
    payload["prompt"] = "绕过结构化字段的自然语言提示词"
    with pytest.raises(ValidationError):
        ShotSpec.model_validate(payload)


def test_prompt_compiler_is_deterministic_and_model_specific() -> None:
    shot = _draft()
    compiler = PromptCompiler()
    compiled = {
        adapter: compiler.compile(
            shot,
            adapter_name=adapter,
            project_lock={"style": "写实悬疑"},
            scene_lock={"location": "便利店"},
            character_locks=[
                {
                    "character_id": "character-linlan",
                    "name": "林岚",
                    "identity_version_id": "identity-v1",
                    "look_version_id": "look-v1",
                    "story_state_version_id": "state-v1",
                }
            ],
        )
        for adapter in ("generic", "veo", "kling", "seedance")
    }

    assert len({item.prompt for item in compiled.values()}) == 4
    assert all(item.compiler_input_hash for item in compiled.values())
    assert all(item.prompt_hash for item in compiled.values())
    repeated = compiler.compile(
        shot,
        adapter_name="veo",
        project_lock={"style": "写实悬疑"},
        scene_lock={"location": "便利店"},
        character_locks=[
            {
                "character_id": "character-linlan",
                "name": "林岚",
                "identity_version_id": "identity-v1",
                "look_version_id": "look-v1",
                "story_state_version_id": "state-v1",
            }
        ],
    )
    assert repeated.compiler_input_hash == compiled["veo"].compiler_input_hash
    assert repeated.prompt_hash == compiled["veo"].prompt_hash


def test_shot_validator_detects_duration_complexity_continuity_and_axis_risk() -> None:
    first_payload = _draft(code="S01-01", duration_sec=1).model_dump(mode="json")
    first_payload["visual_content"]["action"] = "起身，转身，奔跑，开门"
    first_payload["performance"]["characters"][0]["action"] = "起身，转身，奔跑，开门"
    first_payload["camera"]["axis_side"] = "LEFT"
    first = ShotSpec.model_validate(first_payload)

    second_payload = _draft(code="S01-02", duration_sec=2).model_dump(mode="json")
    second_payload["start_state"]["location"] = "便利店门外"
    second_payload["start_state"]["characters"][0]["wardrobe"] = "黑色雨衣"
    second_payload["camera"]["axis_side"] = "RIGHT"
    second = ShotSpec.model_validate(second_payload)

    report = ShotValidator().validate_sequence(
        [first, second],
        expected_total_duration_sec=4,
        expected_scene_durations={1: 4},
    )
    codes = {item.code for item in report.issues}
    assert report.needs_review is True
    assert {
        "ACTION_COMPLEXITY_EXCEEDED",
        "LOCATION_CONTINUITY_BREAK",
        "CHARACTER_WARDROBE_CONTINUITY_BREAK",
        "CAMERA_AXIS_CROSSED",
        "TOTAL_DURATION_MISMATCH",
        "SCENE_DURATION_MISMATCH",
    }.issubset(codes)


@pytest.mark.anyio
async def test_storyboard_agent_returns_strict_deterministic_plan_without_key() -> None:
    result = await generate_storyboard_shot_specs(
        replace(get_settings(), ark_api_key=None),
        [_draft()],
    )

    assert result.provider == "deterministic"
    assert result.diagnostics["strict_schema"] is True
    assert StoryboardShotPlan(shots=result.shots).shots[0].source.code == "S01-01"


@pytest.mark.anyio
async def test_ark_json_strict_schema_repairs_once_then_fails() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=(
                _sse_event("response.output_text.delta", {"delta": "{}"})
                + _sse_event("response.completed", {"response": {}})
                + b"data: [DONE]\n\n"
            ),
        )

    with pytest.raises(TextProviderError) as caught:
        await _ark_json(
            replace(get_settings(), ark_api_key="test-key"),
            prompt="return strict storyboard JSON",
            validator=StoryboardShotPlan,
            transport=httpx.MockTransport(handler),
            max_attempts=2,
            strict_json_schema=True,
        )

    assert len(calls) == 2
    assert len(caught.value.details["attempts"]) == 2
    response_format = calls[0]["text"]["format"]
    assert response_format["type"] == "json_schema"
    assert response_format["strict"] is True
    assert response_format["schema"] == StoryboardShotPlan.model_json_schema()
