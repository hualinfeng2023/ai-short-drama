import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import get_settings
from app.domain.shot_spec import ShotSpec, StoryboardShotPlan
from app.services.prompt_compiler import PromptCompiler
from app.services.shot_action_rewrite import (
    merge_rewritten_actions,
    rewrite_shot_action,
)
from app.services.shot_duration_recommendation import (
    deterministic_duration_recommendation,
    recommend_shot_duration,
)
from app.services.shot_end_state_rewrite import (
    merge_rewritten_end_state,
    rewrite_shot_end_state,
)
from app.services.shot_specs import (
    build_structured_shot_spec,
    ensure_shot_spec_generation_ready,
)
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


def test_generic_prompt_compiles_inherited_context_into_cinematic_image_brief() -> None:
    shot = _draft()
    compiler = PromptCompiler()
    compiled = compiler.compile(
        shot,
        adapter_name="generic",
        project_lock={
            "style": "realistic_cinematic",
            "world": "暴雨淹没城市后的近未来，停电区域依靠应急照明维持运行。",
            "visual_direction": {
                "overall_visual": (
                    "冷灰与脏绿色为主，人物发现真相时才出现微弱暖光。"
                    "孩子出生后，画面转为暖金色。"
                )
            },
        },
        scene_lock={
            "location": {
                "name": "停电后的街角便利店",
                "visual_facts": {
                    "architecture": "狭长店面，入口与收银台位置固定",
                    "materials": ["磨损防滑地砖", "起雾玻璃", "老旧金属货架"],
                    "lighting": "故障应急灯从后场投来冷绿色侧光",
                },
            },
            "props": [
                {
                    "name": "雨水打湿的收据",
                    "visual_facts": {
                        "material": "热敏纸卷曲发黑",
                        "continuity": "右下角撕裂位置固定",
                    },
                }
            ],
        },
        character_locks=[
            {
                "character_id": "character-linlan",
                "name": "林岚",
                "visual_brief": "三十多岁女性，短发被雨水打湿，深色旧风衣，戴无菌检查手套",
                "identity": {"stable_traits": {"face": "窄长脸，左眉尾有浅疤"}},
                "look": {"wardrobe": "深色旧风衣，袖口磨损"},
                "story_state": {"emotion": "警觉、疲惫，刚意识到线索异常"},
            }
        ],
    )
    prompt = compiled.prompt

    assert "暴雨淹没城市后的近未来" in prompt
    assert "磨损防滑地砖" in prompt
    assert "雨水打湿的收据" in prompt
    assert "左眉尾有浅疤" in prompt
    assert "戴无菌检查手套" in prompt
    assert "中近景" in prompt
    assert "65mm中长焦焦段" in prompt
    assert "透视适度压缩" in prompt
    assert "真实电影摄影" in prompt
    assert "实拍置景与物理灯光" in prompt
    assert "光源位置、照射方向、遮挡关系、反射路径与亮度衰减必须彼此一致" in prompt
    assert "亮部柔和滚降" in prompt
    assert "湿润表面产生方向一致的低亮度反射" in prompt
    assert "玻璃同时保留受控反射与透射" in prompt
    assert "金属高光随表面粗糙度变化" in prompt
    assert "概念原画" in prompt
    assert "无设定依据的霓虹灯" in prompt
    assert "9:16" in prompt
    assert "写实电影风格" in prompt
    assert "realistic_cinematic" not in prompt
    assert "孩子出生后" not in prompt
    assert "Project Global Lock" not in prompt
    assert "character-linlan" not in prompt
    assert '{"' not in prompt
    assert prompt.index("世界与场景") < prompt.index("角色连续性")
    assert prompt.index("角色连续性") < prompt.index("摄影机与构图")
    assert compiled.adapter_version == "generic-image-v3"
    assert compiled.compiler_version == "prompt-compiler-v3"


def test_generic_prompt_resolves_camera_language_and_director_note_duplication() -> None:
    payload = _draft().model_dump(mode="json")
    payload["camera"]["shot_size"] = "WS"
    payload["camera"]["lens_mm"] = 50
    payload["camera"]["focus"] = "主体清晰"
    payload["technique"]["notes"] = (
        "导演修改意见：严格表现低温胚胎舱与冷光监测终端；"
        "禁止出现婴儿床、婴儿车。"
        "导演修改意见：严格表现低温胚胎舱与冷光监测终端；"
        "禁止出现婴儿床、婴儿车。"
    )
    prompt = PromptCompiler().compile(
        ShotSpec.model_validate(payload),
        adapter_name="generic",
        project_lock={"style": "realistic_cinematic"},
    ).prompt

    assert "平视机位，全景" in prompt
    assert "50mm标准焦段" in prompt
    assert "自然透视，主体与环境比例不过度夸张" in prompt
    assert "50mm广角" not in prompt
    assert prompt.count("严格表现低温胚胎舱与冷光监测终端") == 1
    assert prompt.count("婴儿床") == 1
    assert prompt.count("婴儿车") == 1


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


def test_shot_validator_blocks_location_prop_semantic_conflict() -> None:
    payload = _draft(duration_sec=4).model_dump(mode="json")
    payload["visual_content"]["environment"] = "未来医疗中心的人类胚胎存储室"
    payload["visual_content"]["description"] = "胚胎舱两侧排列着蒙尘的婴儿床"
    payload["visual_content"]["action"] = "冷凝水沿着胚胎舱玻璃滑落"
    payload["start_state"]["location"] = "未来医疗中心的人类胚胎存储室"
    payload["start_state"]["environment_state"] = "胚胎存储系统低功率运行"
    payload["end_state"]["location"] = "未来医疗中心的人类胚胎存储室"
    payload["end_state"]["environment_state"] = "胚胎存储系统低功率运行"

    issues = ShotValidator().validate_shot(ShotSpec.model_validate(payload))
    semantic_issue = next(
        issue for issue in issues if issue.code == "LOCATION_PROP_SEMANTIC_CONFLICT"
    )

    assert semantic_issue.severity == "BLOCKER"
    assert semantic_issue.repairable is True
    assert semantic_issue.details["conflicting_elements"] == ["婴儿床"]
    assert semantic_issue.details["expected_elements"] == [
        "低温胚胎舱",
        "生命维持管线",
        "监测终端",
    ]


def test_shot_validator_allows_nursery_furniture_outside_embryo_storage() -> None:
    payload = _draft(duration_sec=4).model_dump(mode="json")
    payload["visual_content"]["environment"] = "医院新生儿护理室"
    payload["visual_content"]["description"] = "护理室内整齐排列着婴儿床"
    payload["start_state"]["location"] = "医院新生儿护理室"
    payload["end_state"]["location"] = "医院新生儿护理室"

    issue_codes = {
        issue.code for issue in ShotValidator().validate_shot(ShotSpec.model_validate(payload))
    }

    assert "LOCATION_PROP_SEMANTIC_CONFLICT" not in issue_codes


def test_generation_gate_allows_sequence_budget_issues() -> None:
    spec = SimpleNamespace(
        id="shot-spec-1",
        review_status="NEEDS_REVIEW",
        validation_report_json=json.dumps(
            {
                "issues": [
                    {
                        "code": "TOTAL_DURATION_MISMATCH",
                        "severity": "BLOCKER",
                    },
                    {
                        "code": "SCENE_DURATION_MISMATCH",
                        "severity": "BLOCKER",
                    },
                ]
            }
        ),
    )

    ensure_shot_spec_generation_ready(spec)


def test_generation_gate_blocks_shot_semantic_conflict() -> None:
    spec = SimpleNamespace(
        id="shot-spec-1",
        review_status="NEEDS_REVIEW",
        validation_report_json=json.dumps(
            {
                "issues": [
                    {
                        "code": "LOCATION_PROP_SEMANTIC_CONFLICT",
                        "severity": "BLOCKER",
                    }
                ]
            }
        ),
    )

    with pytest.raises(HTTPException) as error:
        ensure_shot_spec_generation_ready(spec)

    assert error.value.status_code == 409


def test_duration_recommendation_uses_half_second_steps_and_action_complexity() -> None:
    payload = _draft(duration_sec=2).model_dump(mode="json")
    payload["visual_content"]["action"] = "起身，转身，奔跑，开门"
    payload["performance"]["characters"][0]["action"] = "起身，转身，奔跑，开门"

    recommendation = deterministic_duration_recommendation(ShotSpec.model_validate(payload))

    assert recommendation.recommended_duration_sec == 6
    assert recommendation.recommended_min_sec == 5
    assert recommendation.recommended_max_sec == 7
    assert recommendation.recommended_duration_sec * 2 == round(
        recommendation.recommended_duration_sec * 2
    )
    assert recommendation.factors[0] == "4 个可见动作或节拍"


@pytest.mark.anyio
async def test_duration_recommendation_falls_back_without_provider_key() -> None:
    result = await recommend_shot_duration(
        replace(get_settings(), ark_api_key=None),
        _draft(duration_sec=2),
    )

    assert result.provider == "deterministic"
    assert result.model == "shot-duration-rules-v1"
    assert result.recommendation.recommended_duration_sec >= 0.5


@pytest.mark.anyio
async def test_action_rewrite_removes_complexity_blocker_without_provider_key() -> None:
    payload = _draft(duration_sec=2).model_dump(mode="json")
    payload["visual_content"]["action"] = "起身，转身，奔跑，开门"
    payload["performance"]["characters"][0]["action"] = "起身，转身，奔跑，开门"

    result = await rewrite_shot_action(
        replace(get_settings(), ark_api_key=None),
        ShotSpec.model_validate(payload),
    )
    issue_codes = {
        issue.code for issue in ShotValidator().validate_shot(result.shot_spec)
    }

    assert result.provider == "deterministic"
    assert result.resolved_issue_codes == ["ACTION_COMPLEXITY_EXCEEDED"]
    assert "ACTION_COMPLEXITY_EXCEEDED" not in issue_codes
    assert result.shot_spec.duration_sec == 2
    assert result.shot_spec.narrative_goal == payload["narrative_goal"]


@pytest.mark.anyio
async def test_end_state_rewrite_advances_state_without_provider_key() -> None:
    shot = _draft(duration_sec=2)
    result = await rewrite_shot_end_state(
        replace(get_settings(), ark_api_key=None),
        shot,
    )
    issue_codes = {
        issue.code for issue in ShotValidator().validate_shot(result.shot_spec)
    }

    assert result.provider == "deterministic"
    assert result.resolved_issue_codes == ["END_STATE_NOT_ADVANCED"]
    assert "END_STATE_NOT_ADVANCED" not in issue_codes
    assert result.shot_spec.end_state.action_state != shot.start_state.action_state


def test_end_state_rewrite_merge_changes_only_end_state_action() -> None:
    current = _draft(duration_sec=2)
    rewritten_payload = current.model_dump(mode="json")
    rewritten_payload["duration_sec"] = 9
    rewritten_payload["visual_content"]["action"] = "林岚举起收据"
    rewritten_payload["end_state"]["action_state"] = "林岚举起收据后停在门前"

    merged = merge_rewritten_end_state(
        current,
        ShotSpec.model_validate(rewritten_payload),
    )

    assert merged.duration_sec == current.duration_sec
    assert merged.visual_content.action == current.visual_content.action
    assert merged.end_state.action_state == "林岚举起收据后停在门前"


def test_action_rewrite_merge_changes_only_action_fields() -> None:
    current = _draft(duration_sec=2)
    rewritten_payload = current.model_dump(mode="json")
    rewritten_payload["duration_sec"] = 9
    rewritten_payload["narrative_goal"] = "不应覆盖的叙事目标"
    rewritten_payload["visual_content"]["action"] = "林岚举起收据"
    rewritten_payload["performance"]["characters"][0]["action"] = "举起收据"

    merged = merge_rewritten_actions(
        current,
        ShotSpec.model_validate(rewritten_payload),
    )

    assert merged.visual_content.action == "林岚举起收据"
    assert merged.performance.characters[0].action == "举起收据"
    assert merged.duration_sec == 2
    assert merged.narrative_goal == current.narrative_goal


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
