import json
from typing import Any, Literal

NarrativeProtagonist = Literal["unspecified", "male", "female", "dual", "ensemble"]
TargetAudience = Literal["male_frequency", "female_frequency", "general"]
EmotionalReward = Literal[
    "romance",
    "identity",
    "career",
    "revenge",
    "family",
    "power",
    "public_mission",
]
ProductionFormat = Literal["live_action", "ai_comic", "high_concept_fantasy"]

TOPIC_SLATE_MIX: dict[ProductionFormat, dict[TargetAudience, int]] = {
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


def topic_slate_mix(production_format: ProductionFormat) -> dict[TargetAudience, int]:
    return dict(TOPIC_SLATE_MIX[production_format])


def targeting_from_brief(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "narrative_protagonist": str(brief.get("narrative_protagonist", "unspecified")),
        "target_audience": str(brief.get("target_audience", "general")),
        "emotional_rewards": [str(item) for item in brief.get("emotional_rewards", [])],
        "audience_profile": str(brief.get("audience_profile", "")),
        "production_format": str(brief.get("production_format", "live_action")),
    }


def incomplete_targeting_fields(brief: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if brief.get("narrative_protagonist", "unspecified") == "unspecified":
        missing.append("叙事主角")
    if not brief.get("emotional_rewards"):
        missing.append("情绪回报")
    return missing


def targeting_prompt_guardrails(brief: dict[str, Any]) -> str:
    targeting = targeting_from_brief(brief)
    slate_mix = topic_slate_mix(targeting["production_format"])
    return (
        "\n叙事定位是五个彼此独立的输入，必须逐项遵守，禁止相互推断：\n"
        f"- 叙事主角：{targeting['narrative_protagonist']}\n"
        f"- 目标受众：{targeting['target_audience']}\n"
        f"- 情绪回报：{json.dumps(targeting['emotional_rewards'], ensure_ascii=False)}\n"
        f"- 补充受众画像：{targeting['audience_profile'] or '未指定'}\n"
        f"- 内容形态：{targeting['production_format']}\n"
        "硬性去偏置规则：\n"
        "1. 主角性别只定义叙事视角，不得自动推导题材、职业、能力、阶层、关系模式或爽点。\n"
        "2. 男性主角不得自动写成战神、赘婿、后宫或同类男频模板；"
        "仅当用户原始故事明确要求时才可使用。\n"
        "3. 女性受众不得自动推导女性主角或大女主；目标受众只影响信息组织与情绪表达。\n"
        "4. 女性主角、男性受众、双主角与群像均可和任意情绪回报自由组合。\n"
        "5. 补充受众画像只用于项目层表达校准，不能改写叙事主角、目标受众或情绪回报。\n"
        f"6. 当前内容形态的首批选题池配比仅用于项目组合规划，不得改写本项目：{slate_mix}。\n"
    )


def _creative_text(value: Any, *, parent_key: str = "") -> str:
    excluded = {"brief_compliance", "critic", "risk_notes", "assumptions"}
    if parent_key in excluded:
        return ""
    if isinstance(value, dict):
        return " ".join(_creative_text(item, parent_key=str(key)) for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_creative_text(item, parent_key=parent_key) for item in value)
    return value if isinstance(value, str) else ""


def reject_unrequested_stereotypes(payload: dict[str, Any], brief: dict[str, Any]) -> None:
    requested_text = " ".join(
        [
            str(brief.get("raw_input", "")),
            *[str(item) for item in brief.get("content_requirements", [])],
        ]
    )
    generated_text = _creative_text(payload)
    checks: list[tuple[bool, tuple[str, ...]]] = [
        (
            brief.get("narrative_protagonist") == "male",
            ("战神", "赘婿", "后宫"),
        ),
        (
            brief.get("target_audience") == "female_frequency",
            ("大女主",),
        ),
    ]
    violations = [
        term
        for enabled, terms in checks
        if enabled
        for term in terms
        if term in generated_text and term not in requested_text
    ]
    if violations:
        raise ValueError(f"生成内容引入了未经用户要求的类型刻板映射：{', '.join(violations)}")
