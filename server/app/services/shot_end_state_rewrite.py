import asyncio
import json

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.domain.shot_spec import ShotSpec
from app.services.text_provider import TextProviderError, _ark_json


class ShotEndStateRewritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rewritten_action_state: str = Field(min_length=1, max_length=600)
    reason: str = Field(min_length=1, max_length=240)


class ShotEndStateRewriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_spec: ShotSpec
    reason: str
    resolved_issue_codes: list[str]
    provider: str
    model: str
    request_id: str | None = None


def _apply_rewrite(shot: ShotSpec, payload: ShotEndStateRewritePayload) -> ShotSpec:
    return shot.model_copy(
        update={
            "end_state": shot.end_state.model_copy(
                update={"action_state": payload.rewritten_action_state}
            )
        }
    )


def merge_rewritten_end_state(current: ShotSpec, rewritten: ShotSpec) -> ShotSpec:
    """Apply only the validator-targeted end-state action field."""
    return current.model_copy(
        update={
            "end_state": current.end_state.model_copy(
                update={"action_state": rewritten.end_state.action_state}
            )
        }
    )


def deterministic_shot_end_state_rewrite(shot: ShotSpec) -> ShotEndStateRewritePayload:
    return ShotEndStateRewritePayload(
        rewritten_action_state=f"动作完成后，{shot.visual_content.action}",
        reason="补充动作发生后的可见状态，明确镜头结尾的变化结果。",
    )


async def rewrite_shot_end_state(
    settings: Settings,
    shot: ShotSpec,
) -> ShotEndStateRewriteResult:
    fallback_payload = deterministic_shot_end_state_rewrite(shot)
    fallback_shot = _apply_rewrite(shot, fallback_payload)
    if not settings.ark_api_key:
        return ShotEndStateRewriteResult(
            shot_spec=fallback_shot,
            reason=fallback_payload.reason,
            resolved_issue_codes=["END_STATE_NOT_ADVANCED"],
            provider="deterministic",
            model="shot-end-state-rewrite-rules-v1",
        )

    prompt = (
        "你是短剧分镜结尾状态优化器。只补全 end_state.action_state，不改变人物身份、"
        "地点、道具、时间、镜头语言和剧情事实。结尾状态必须具体描述当前画面动作完成后，"
        "人物、物体或环境可见的变化；不能与 start_state.action_state 相同，不得加入输入中"
        "没有的事实。reason 使用自然简体中文，不超过 80 字。"
        "\n输出必须符合 JSON Schema：\n"
        f"{json.dumps(ShotEndStateRewritePayload.model_json_schema(), ensure_ascii=False)}"
        "\n当前镜头 ShotSpec：\n"
        f"{json.dumps(shot.model_dump(mode='json'), ensure_ascii=False)}"
    )

    def validate_rewrite(value: BaseModel) -> None:
        payload = ShotEndStateRewritePayload.model_validate(value)
        if payload.rewritten_action_state == shot.start_state.action_state:
            raise ValueError("结尾动作状态不能与起始状态相同")

    try:
        async with asyncio.timeout(8):
            generated = await _ark_json(
                settings,
                prompt=prompt,
                validator=ShotEndStateRewritePayload,
                semantic_validator=validate_rewrite,
                max_attempts=2,
                strict_json_schema=True,
            )
        generated_payload = ShotEndStateRewritePayload.model_validate(generated.payload)
        return ShotEndStateRewriteResult(
            shot_spec=_apply_rewrite(shot, generated_payload),
            reason=generated_payload.reason,
            resolved_issue_codes=["END_STATE_NOT_ADVANCED"],
            provider=generated.provider,
            model=generated.model,
            request_id=generated.request_id,
        )
    except (TextProviderError, TimeoutError):
        return ShotEndStateRewriteResult(
            shot_spec=fallback_shot,
            reason=fallback_payload.reason,
            resolved_issue_codes=["END_STATE_NOT_ADVANCED"],
            provider="deterministic",
            model="shot-end-state-rewrite-rules-v1",
        )
