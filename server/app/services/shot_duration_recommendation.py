import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import Settings
from app.domain.shot_spec import ShotSpec
from app.services.text_provider import TextProviderError, _ark_json


class ShotDurationRecommendationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    recommended_duration_sec: float = Field(ge=0.5, le=12)
    recommended_min_sec: float = Field(ge=0.5, le=12)
    recommended_max_sec: float = Field(ge=0.5, le=12)
    reason: str = Field(min_length=1, max_length=240)
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    factors: list[str] = Field(default_factory=list, max_length=3)

    @field_validator(
        "recommended_duration_sec",
        "recommended_min_sec",
        "recommended_max_sec",
    )
    @classmethod
    def require_half_second_step(cls, value: float) -> float:
        if not math.isclose(value * 2, round(value * 2), abs_tol=1e-6):
            raise ValueError("推荐时长必须以 0.5 秒为步长")
        return value

    @model_validator(mode="after")
    def validate_recommended_range(self) -> "ShotDurationRecommendationPayload":
        if self.recommended_min_sec > self.recommended_max_sec:
            raise ValueError("推荐时长范围的最小值不能大于最大值")
        if not self.recommended_min_sec <= self.recommended_duration_sec <= self.recommended_max_sec:
            raise ValueError("最佳时长必须位于推荐范围内")
        return self


class ShotDurationRecommendationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: ShotDurationRecommendationPayload
    provider: str
    model: str
    request_id: str | None = None


_ACTION_BREAKS = re.compile(r"[，,；;、。.!！？?]|\b(?:then|while|and then)\b", re.IGNORECASE)
_CJK = re.compile(r"[\u3400-\u9fff]")
_LATIN_WORD = re.compile(r"\b[\w'-]+\b")


def _action_count(shot: ShotSpec) -> int:
    visual_actions = [
        item.strip()
        for item in _ACTION_BREAKS.split(shot.visual_content.action)
        if item.strip()
    ]
    performance_actions = sum(
        max(
            1,
            len(
                [
                    item
                    for item in _ACTION_BREAKS.split(character.action)
                    if item.strip()
                ]
            ),
        )
        for character in shot.performance.characters
    )
    return max(1, len(visual_actions), performance_actions)


def _spoken_duration_sec(shot: ShotSpec) -> float:
    dialogue = " ".join(
        item.strip()
        for item in (shot.audio.dialogue, shot.audio.voice_over)
        if item.strip()
    )
    if not dialogue:
        return 0
    cjk_count = len(_CJK.findall(dialogue))
    latin_words = len(_LATIN_WORD.findall(dialogue))
    return cjk_count / 4.2 + latin_words / 2.5 + 0.5


def deterministic_duration_recommendation(
    shot: ShotSpec,
) -> ShotDurationRecommendationPayload:
    action_count = _action_count(shot)
    spoken_duration = _spoken_duration_sec(shot)
    state_transition = (
        shot.start_state.location != shot.end_state.location
        or shot.start_state.action_state != shot.end_state.action_state
    )
    action_window = min(action_count, 6) * 1.5
    raw_duration = max(1.5, action_window, spoken_duration, 2.0 if state_transition else 0)
    recommended = min(12.0, math.ceil(raw_duration * 2) / 2)

    factors = [f"{action_count} 个可见动作或节拍"]
    if spoken_duration:
        factors.append(f"对白自然表达约需 {spoken_duration:.1f} 秒")
    if state_transition:
        factors.append("需要交代首尾状态变化")

    if action_count > 6:
        reason = (
            f"建议先按 {recommended:g} 秒预留生成窗口；当前动作仍偏多，"
            "同时精简为一个主要动作会更稳定。"
        )
        confidence: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"
    elif spoken_duration >= action_window:
        reason = f"建议 {recommended:g} 秒，让对白完整落地并保留必要停顿。"
        confidence = "HIGH"
    else:
        reason = f"建议 {recommended:g} 秒，为主要动作和结尾状态留出稳定呈现时间。"
        confidence = "MEDIUM"
    range_margin = 0.5 if confidence == "HIGH" else 1.0 if confidence == "MEDIUM" else 1.5
    recommended_min = max(0.5, recommended - range_margin)
    recommended_max = min(12.0, recommended + range_margin)

    return ShotDurationRecommendationPayload(
        recommended_duration_sec=recommended,
        recommended_min_sec=recommended_min,
        recommended_max_sec=recommended_max,
        reason=reason,
        confidence=confidence,
        factors=factors[:3],
    )


async def recommend_shot_duration(
    settings: Settings,
    shot: ShotSpec,
) -> ShotDurationRecommendationResult:
    fallback = deterministic_duration_recommendation(shot)
    if not settings.ark_api_key:
        return ShotDurationRecommendationResult(
            recommendation=fallback,
            provider="deterministic",
            model="shot-duration-rules-v1",
        )

    prompt = (
        "你是短剧分镜时长顾问。只评估当前单个镜头需要多少秒，不改写镜头内容。"
        "综合判断可见动作数量、动作连续性、对白自然语速、首尾状态变化、运镜复杂度和"
        "视频生成稳定性。推荐值必须在 0.5 至 12 秒之间，并且只能使用 0.5 秒步长。"
        "同时给出 recommended_min_sec 和 recommended_max_sec，表示不牺牲叙事清晰度与"
        "生成稳定性的可接受区间；recommended_duration_sec 必须位于该区间内。"
        "如果仅延长时长仍不足以稳定呈现，必须在 reason 中明确建议精简动作。"
        "reason 使用自然简体中文，不超过 80 字；factors 最多 3 项，不得编造输入外事实。"
        "\n输出必须符合 JSON Schema：\n"
        f"{json.dumps(ShotDurationRecommendationPayload.model_json_schema(), ensure_ascii=False)}"
        "\n当前镜头 ShotSpec：\n"
        f"{json.dumps(shot.model_dump(mode='json'), ensure_ascii=False)}"
    )
    try:
        generated = await _ark_json(
            settings,
            prompt=prompt,
            validator=ShotDurationRecommendationPayload,
            max_attempts=2,
            strict_json_schema=True,
        )
    except TextProviderError:
        return ShotDurationRecommendationResult(
            recommendation=fallback,
            provider="deterministic",
            model="shot-duration-rules-v1",
        )

    return ShotDurationRecommendationResult(
        recommendation=ShotDurationRecommendationPayload.model_validate(generated.payload),
        provider=generated.provider,
        model=generated.model,
        request_id=generated.request_id,
    )
