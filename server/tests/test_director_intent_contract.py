import copy

import pytest
from pydantic import ValidationError

from app.domain.director_intent import (
    DirectorIntent,
    DirectorIntentChangePreview,
)

PROJECT_ID = "10000000-0000-4000-8000-000000000001"
INTENT_ID = "10000000-0000-4000-8000-000000000002"
SCENE_ID = "10000000-0000-4000-8000-000000000003"


def intent_payload() -> dict[str, object]:
    scene_ref = {
        "type": "ScriptScene",
        "id": SCENE_ID,
        "version_id": "script-v7",
        "content_hash": "scene-hash-v7",
    }
    beat_ref = {
        "type": "StoryBeat",
        "id": "beat:episode-1:4",
        "version_id": "story-v3",
        "field_path": "short_drama_engine.beats[3]",
        "content_hash": "beat-hash-v3",
    }
    goal_ref = {
        "type": "CharacterGoal",
        "id": "character-goal:lin-xia:scene-4",
        "version_id": "character-state-v5",
        "field_path": "current_goal",
        "content_hash": "goal-hash-v5",
    }
    evidence = [
        {
            "evidence_id": "ev-beat",
            "source": beat_ref,
            "claim": "该情节点的功能是迫使主角在信息不足时立即选择。",
            "confidence": 0.91,
            "time_range": {"start_ms": 42_000, "end_ms": 50_000},
        },
        {
            "evidence_id": "ev-goal",
            "source": goal_ref,
            "claim": "主角当前目标是阻止对方离开，而不是解释前因。",
            "confidence": 0.88,
            "time_range": {"start_ms": 42_000, "end_ms": 50_000},
        },
        {
            "evidence_id": "ev-scene",
            "source": scene_ref,
            "claim": "当前场景窗口为 8 秒，解释性对白占据主要时长。",
            "confidence": 0.94,
            "time_range": {"start_ms": 42_000, "end_ms": 50_000},
        },
    ]
    directives = [
        {
            "channel": "NARRATIVE",
            "status": "CHANGE",
            "instruction": "冲突提前，删除不影响因果成立的解释。",
            "observable_effect": "人物在场景前半段即采取阻拦行动。",
            "confidence": 0.88,
            "evidence_refs": ["ev-beat", "ev-goal"],
        },
        {
            "channel": "CAMERA",
            "status": "CHANGE",
            "instruction": "中景推进到近景，减少人物周围负空间。",
            "observable_effect": "景别在目标行动出现时收紧，环境信息让位于人物压力。",
            "confidence": 0.76,
            "evidence_refs": ["ev-beat", "ev-scene"],
        },
        {
            "channel": "PERFORMANCE",
            "status": "CHANGE",
            "instruction": "缩短反应停顿，增加急促呼吸与克制的手部动作。",
            "observable_effect": "停顿缩短，压力通过呼吸和动作可见。",
            "confidence": 0.82,
            "evidence_refs": ["ev-goal", "ev-scene"],
        },
        {
            "channel": "SOUND",
            "status": "CHANGE",
            "instruction": "降低环境声，加入克制的低频压力层。",
            "observable_effect": "对白前后的环境声变薄，低频随冲突提前进入。",
            "confidence": 0.73,
            "evidence_refs": ["ev-beat", "ev-scene"],
        },
        {
            "channel": "PACING",
            "status": "CHANGE",
            "instruction": "将主要镜头时长从 4 秒压缩到 2.5 秒。",
            "observable_effect": "相同情节信息在更短窗口内完成。",
            "confidence": 0.86,
            "evidence_refs": ["ev-scene"],
        },
    ]
    return {
        "schema_version": "director-intent-v1",
        "intent_id": INTENT_ID,
        "intent_version": 1,
        "project_id": PROJECT_ID,
        "source_request": "这里更紧张",
        "source_request_language": "zh-CN",
        "scope": {
            "resolution_status": "RESOLVED",
            "scene": scene_ref,
            "plot_beat": beat_ref,
            "character_goals": [goal_ref],
            "time_range": {"start_ms": 42_000, "end_ms": 50_000},
            "candidate_targets": [],
            "resolution_reason": "“这里”指向当前选中场景内主角阻拦对方离开的情节点。",
            "context_fingerprint": "context-fingerprint-v7",
        },
        "evidence": evidence,
        "directives": directives,
        "rationale": "提前行动并收紧视听窗口，可减少解释造成的泄压，同时不改人物目标。",
        "overall_confidence": 0.81,
        "conflict_checks": [
            {
                "code": "LOCKED_CHARACTER_PRESERVED",
                "category": "CHARACTER_LOCK",
                "severity": "BLOCKING",
                "status": "PASS",
                "message": "未修改已锁定角色身份、外观或关系。",
                "evidence_refs": ["ev-goal"],
            },
            {
                "code": "WORLD_RULE_PRESERVED",
                "category": "WORLD_RULE",
                "severity": "BLOCKING",
                "status": "PASS",
                "message": "未新增或改写世界规则。",
                "evidence_refs": ["ev-beat"],
            },
            {
                "code": "FILM_TERMS_GROUNDED",
                "category": "TERM_GROUNDING",
                "severity": "BLOCKING",
                "status": "PASS",
                "message": "摄影与声音术语均有可观察效果和当前情境证据。",
                "evidence_refs": ["ev-beat", "ev-scene"],
            },
        ],
        "inheritance_targets": [
            {
                "consumer": "STORYBOARD",
                "status": "PENDING",
                "inherited_fields": ["NARRATIVE", "CAMERA", "PERFORMANCE", "PACING"],
            },
            {
                "consumer": "PROMPT",
                "status": "PENDING",
                "inherited_fields": ["CAMERA", "PERFORMANCE"],
            },
            {
                "consumer": "AUDIO",
                "status": "PENDING",
                "inherited_fields": ["PERFORMANCE", "SOUND", "PACING"],
            },
            {
                "consumer": "TIMELINE",
                "status": "PENDING",
                "inherited_fields": ["SOUND", "PACING"],
            },
        ],
        "state": "PREVIEW_READY",
        "can_confirm": True,
        "blocked_reasons": [],
    }


def test_director_intent_accepts_grounded_confirmable_preview() -> None:
    intent = DirectorIntent.model_validate(intent_payload())

    assert intent.scope.plot_beat is not None
    assert intent.scope.time_range is not None
    assert {item.channel for item in intent.directives} == {
        "NARRATIVE",
        "CAMERA",
        "PERFORMANCE",
        "SOUND",
        "PACING",
    }
    assert {item.consumer for item in intent.inheritance_targets} == {
        "STORYBOARD",
        "PROMPT",
        "AUDIO",
        "TIMELINE",
    }
    assert intent.can_confirm is True


def test_resolved_scope_requires_plot_beat_character_goal_and_time_range() -> None:
    payload = intent_payload()
    payload["scope"] = {
        **payload["scope"],  # type: ignore[arg-type]
        "plot_beat": None,
        "character_goals": [],
        "time_range": None,
    }

    with pytest.raises(ValidationError) as caught:
        DirectorIntent.model_validate(payload)

    assert "必须绑定 plot_beat" in str(caught.value)


def test_blocking_unknown_conflict_must_fail_closed() -> None:
    payload = intent_payload()
    payload["conflict_checks"][0]["status"] = "UNKNOWN"  # type: ignore[index]

    with pytest.raises(ValidationError) as caught:
        DirectorIntent.model_validate(payload)

    assert "can_confirm 与作用域、置信度、冲突和通道解析结果不一致" in str(
        caught.value
    )

    payload["can_confirm"] = False
    payload["state"] = "BLOCKED"
    payload["blocked_reasons"] = ["无法确认已锁定角色是否保持不变"]
    blocked = DirectorIntent.model_validate(payload)
    assert blocked.can_confirm is False


def test_director_intent_rejects_ungrounded_professional_term() -> None:
    payload = intent_payload()
    payload["directives"][1]["evidence_refs"] = ["ev-does-not-exist"]  # type: ignore[index]

    with pytest.raises(ValidationError) as caught:
        DirectorIntent.model_validate(payload)

    assert "引用了不存在的证据" in str(caught.value)


def test_change_preview_is_read_only_and_uses_same_evidence_receipt() -> None:
    intent = DirectorIntent.model_validate(intent_payload())
    sections = [
        {
            "channel": directive.channel,
            "before": "当前方案",
            "after": directive.instruction,
            "why": directive.observable_effect,
            "confidence": directive.confidence,
            "evidence_refs": directive.evidence_refs,
        }
        for directive in intent.directives
    ]
    preview = DirectorIntentChangePreview.model_validate(
        {
            "intent": intent.model_dump(mode="json"),
            "sections": sections,
            "preserved_invariants": ["角色身份不变", "人物目标不变", "世界规则不变"],
            "downstream_summary": [
                "确认后由分镜、提示词、音频和时间线读取同一 intent_version。",
                "当前预览不修改正式时间线，也不触发媒体生成。",
            ],
        }
    )

    assert preview.projection_mode == "READ_ONLY"
    assert preview.canonical_source == "FILM_IR"
    assert preview.intent.source_request == "这里更紧张"


def test_change_preview_requires_all_five_channels() -> None:
    intent = DirectorIntent.model_validate(intent_payload())
    payload = {
        "intent": intent.model_dump(mode="json"),
        "sections": [
            {
                "channel": directive.channel,
                "before": "当前方案",
                "after": directive.instruction,
                "why": directive.observable_effect,
                "confidence": directive.confidence,
                "evidence_refs": directive.evidence_refs,
            }
            for directive in intent.directives
        ],
        "preserved_invariants": ["角色身份不变"],
        "downstream_summary": ["只读预览"],
    }
    duplicated = copy.deepcopy(payload)
    duplicated["sections"][4]["channel"] = "NARRATIVE"

    with pytest.raises(ValidationError) as caught:
        DirectorIntentChangePreview.model_validate(duplicated)

    assert "必须且只能展示五个专业通道" in str(caught.value)
