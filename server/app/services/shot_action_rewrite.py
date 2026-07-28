import asyncio
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.domain.shot_spec import ShotSpec
from app.services.shot_validator import ShotValidator
from app.services.text_provider import (
    ModelOutputSemanticError,
    TextProviderError,
    _ark_json,
)


class CharacterActionRewrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    character_id: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=600)


class ShotActionRewritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rewritten_action: str = Field(min_length=1, max_length=1200)
    character_actions: list[CharacterActionRewrite] = Field(
        default_factory=list,
        max_length=12,
    )
    reason: str = Field(min_length=1, max_length=240)


class ShotActionRewriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_spec: ShotSpec
    reason: str
    resolved_issue_codes: list[str]
    provider: str
    model: str
    request_id: str | None = None


_ACTION_BREAKS = re.compile(r"[，,；;、。.!！？?]|\b(?:then|while|and then)\b", re.IGNORECASE)
_META_TOKENS = (
    "开场",
    "交代",
    "展示",
    "强化",
    "推进",
    "完成",
    "铺垫",
    "核心钩子",
    "背景设定",
)
_CONCRETE_TOKENS = (
    "镜头",
    "人物",
    "手",
    "眼",
    "门",
    "光",
    "水",
    "玻璃",
    "走",
    "拿",
    "看",
    "抬",
    "转",
    "落",
    "亮",
    "响",
)


def _single_action(value: str) -> str:
    parts = [item.strip() for item in _ACTION_BREAKS.split(value) if item.strip()]
    if not parts:
        return value.strip()

    def score(part: str) -> tuple[int, int]:
        concrete = sum(token in part for token in _CONCRETE_TOKENS)
        meta = sum(token in part for token in _META_TOKENS)
        return concrete * 8 - meta * 12, len(part)

    return max(parts, key=score)


def _apply_rewrite(shot: ShotSpec, payload: ShotActionRewritePayload) -> ShotSpec:
    expected_character_ids = {
        character.character_id for character in shot.performance.characters
    }
    action_by_character = {
        character.character_id: character.action
        for character in payload.character_actions
    }
    if set(action_by_character) != expected_character_ids:
        raise ModelOutputSemanticError(
            "SHOT_ACTION_CHARACTER_SCOPE_INVALID",
            "AI 改写返回了错误的角色动作范围",
            repair_message=(
                "character_actions 必须与输入 performance.characters 的 character_id "
                "完全一致，不得增加、删除或替换角色。"
            ),
            details={
                "expected_character_ids": sorted(expected_character_ids),
                "actual_character_ids": sorted(action_by_character),
            },
        )
    visual_content = shot.visual_content.model_copy(
        update={"action": payload.rewritten_action}
    )
    performance = shot.performance.model_copy(
        update={
            "characters": [
                character.model_copy(
                    update={"action": action_by_character[character.character_id]}
                )
                for character in shot.performance.characters
            ]
        }
    )
    return shot.model_copy(
        update={
            "visual_content": visual_content,
            "performance": performance,
        }
    )


def merge_rewritten_actions(current: ShotSpec, rewritten: ShotSpec) -> ShotSpec:
    """Apply only the validator-targeted action fields to the persisted ShotSpec."""
    rewritten_by_character = {
        character.character_id: character.action
        for character in rewritten.performance.characters
    }
    expected_character_ids = {
        character.character_id for character in current.performance.characters
    }
    if set(rewritten_by_character) != expected_character_ids:
        raise ModelOutputSemanticError(
            "SHOT_ACTION_CHARACTER_SCOPE_INVALID",
            "AI 改写返回了错误的角色动作范围",
            details={
                "expected_character_ids": sorted(expected_character_ids),
                "actual_character_ids": sorted(rewritten_by_character),
            },
        )
    return current.model_copy(
        update={
            "visual_content": current.visual_content.model_copy(
                update={"action": rewritten.visual_content.action}
            ),
            "performance": current.performance.model_copy(
                update={
                    "characters": [
                        character.model_copy(
                            update={
                                "action": rewritten_by_character[character.character_id]
                            }
                        )
                        for character in current.performance.characters
                    ]
                }
            ),
        }
    )


def _action_blockers(shot: ShotSpec) -> list[dict[str, Any]]:
    return [
        issue.model_dump(mode="json")
        for issue in ShotValidator().validate_shot(shot)
        if issue.code == "ACTION_COMPLEXITY_EXCEEDED"
    ]


def deterministic_shot_action_rewrite(shot: ShotSpec) -> ShotActionRewritePayload:
    return ShotActionRewritePayload(
        rewritten_action=_single_action(shot.visual_content.action),
        character_actions=[
            CharacterActionRewrite(
                character_id=character.character_id,
                action=_single_action(character.action),
            )
            for character in shot.performance.characters
        ],
        reason="保留一个最具画面辨识度的主要动作，降低单镜连续变化数量。",
    )


async def rewrite_shot_action(
    settings: Settings,
    shot: ShotSpec,
) -> ShotActionRewriteResult:
    fallback_payload = deterministic_shot_action_rewrite(shot)
    fallback_shot = _apply_rewrite(shot, fallback_payload)
    if _action_blockers(fallback_shot):
        raise ModelOutputSemanticError(
            "SHOT_ACTION_FALLBACK_INVALID",
            "本地动作精简未通过 ShotValidator",
            repair_message="必须进一步精简为一个不含连续标点分隔的主要可见动作。",
        )

    if not settings.ark_api_key:
        return ShotActionRewriteResult(
            shot_spec=fallback_shot,
            reason=fallback_payload.reason,
            resolved_issue_codes=["ACTION_COMPLEXITY_EXCEEDED"],
            provider="deterministic",
            model="shot-action-rewrite-rules-v1",
        )

    prompt = (
        "你是短剧分镜动作精简器。目标是在不改变剧情事实、人物身份、道具、地点、"
        "镜头语言和叙事目标的前提下，把当前镜头精简为一个主要可见动作，使其能在"
        f"{shot.duration_sec:g} 秒内稳定生成。"
        "rewritten_action 只能描述画面中实际可见的单一动作，不得使用逗号、分号、"
        "顿号串联多个步骤，不得加入输入中没有的事实。"
        "character_actions 必须覆盖输入 performance.characters 的全部 character_id，"
        "每个角色也只能保留一个动作；没有角色时必须返回空数组。"
        "reason 使用自然简体中文，不超过 80 字。"
        "\n输出必须符合 JSON Schema：\n"
        f"{json.dumps(ShotActionRewritePayload.model_json_schema(), ensure_ascii=False)}"
        "\n当前镜头 ShotSpec：\n"
        f"{json.dumps(shot.model_dump(mode='json'), ensure_ascii=False)}"
    )

    def validate_rewrite(value: BaseModel) -> None:
        payload = ShotActionRewritePayload.model_validate(value)
        candidate = _apply_rewrite(shot, payload)
        blockers = _action_blockers(candidate)
        if blockers:
            raise ModelOutputSemanticError(
                "SHOT_ACTION_REWRITE_STILL_BLOCKED",
                "AI 改写后仍包含过多连续动作",
                repair_message=(
                    "rewritten_action 和每个 character_actions.action 都必须进一步精简，"
                    "只能保留一个主要可见动作。"
                ),
                details={"issues": blockers},
            )

    try:
        async with asyncio.timeout(8):
            generated = await _ark_json(
                settings,
                prompt=prompt,
                validator=ShotActionRewritePayload,
                semantic_validator=validate_rewrite,
                max_attempts=2,
                strict_json_schema=True,
            )
        generated_payload = ShotActionRewritePayload.model_validate(generated.payload)
        rewritten_shot = _apply_rewrite(shot, generated_payload)
        return ShotActionRewriteResult(
            shot_spec=rewritten_shot,
            reason=generated_payload.reason,
            resolved_issue_codes=["ACTION_COMPLEXITY_EXCEEDED"],
            provider=generated.provider,
            model=generated.model,
            request_id=generated.request_id,
        )
    except (TextProviderError, TimeoutError):
        return ShotActionRewriteResult(
            shot_spec=fallback_shot,
            reason=fallback_payload.reason,
            resolved_issue_codes=["ACTION_COMPLEXITY_EXCEEDED"],
            provider="deterministic",
            model="shot-action-rewrite-rules-v1",
        )
