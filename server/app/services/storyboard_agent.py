import json
from dataclasses import dataclass

from pydantic import BaseModel

from app.config import Settings
from app.domain.shot_spec import ShotSpec, StoryboardShotPlan
from app.services.shot_validator import ShotValidationReport, ShotValidator
from app.services.text_provider import (
    ModelOutputSemanticError,
    TextProviderError,
    _ark_json,
)


@dataclass(frozen=True)
class StoryboardAgentResult:
    shots: list[ShotSpec]
    provider: str
    model: str
    request_id: str | None
    repair_attempts: int
    validation_report: ShotValidationReport
    needs_review: bool
    diagnostics: dict[str, object]


def _expected_scene_durations(drafts: list[ShotSpec]) -> dict[int, float]:
    values: dict[int, float] = {}
    for shot in drafts:
        ordinal = shot.source.scene_ordinal
        values[ordinal] = round(values.get(ordinal, 0) + shot.duration_sec, 3)
    return values


def _validate_agent_plan(
    value: BaseModel,
    *,
    drafts: list[ShotSpec],
) -> ShotValidationReport:
    if not isinstance(value, StoryboardShotPlan):
        raise TypeError("Storyboard Agent 只接受 StoryboardShotPlan")
    expected_codes = [item.source.code for item in drafts]
    actual_codes = [item.source.code for item in value.shots]
    structural_issues: list[dict[str, object]] = []
    if actual_codes != expected_codes:
        structural_issues.append(
            {
                "code": "SHOT_SOURCE_ORDER_CHANGED",
                "field_path": "shots",
                "message": "不得增加、删除、重排镜头或改写 source.code",
                "expected": expected_codes,
                "actual": actual_codes,
            }
        )
    expected_sources = [item.source.script_line_ids for item in drafts]
    actual_sources = [item.source.script_line_ids for item in value.shots]
    if actual_sources != expected_sources:
        structural_issues.append(
            {
                "code": "SCRIPT_LINE_BINDING_CHANGED",
                "field_path": "shots.source.script_line_ids",
                "message": "不得改写镜头与剧本台词的绑定",
            }
        )
    validator = ShotValidator()
    report = validator.validate_sequence(
        value.shots,
        expected_total_duration_sec=sum(item.duration_sec for item in drafts),
        expected_scene_durations=_expected_scene_durations(drafts),
    )
    blockers = [
        item.model_dump(mode="json")
        for item in report.issues
        if item.severity == "BLOCKER"
    ]
    if structural_issues or blockers:
        issues = [*structural_issues, *blockers]
        raise ModelOutputSemanticError(
            "STORYBOARD_SHOT_SPEC_INVALID",
            "Storyboard Agent 返回的 ShotSpec 未通过导演级校验",
            repair_message=(
                "只能修复以下点名字段，不得改写 source、镜头数量、顺序和时长总量：\n"
                + "\n".join(
                    f"- {item.get('field_path')} | {item.get('code')} | {item.get('message')}"
                    for item in issues
                )
            ),
            details={"issues": issues},
        )
    return report


async def generate_storyboard_shot_specs(
    settings: Settings,
    drafts: list[ShotSpec],
) -> StoryboardAgentResult:
    deterministic_plan = StoryboardShotPlan(shots=drafts)
    deterministic_report = ShotValidator().validate_sequence(
        drafts,
        expected_total_duration_sec=sum(item.duration_sec for item in drafts),
        expected_scene_durations=_expected_scene_durations(drafts),
    )
    if not settings.ark_api_key:
        return StoryboardAgentResult(
            shots=deterministic_plan.shots,
            provider="deterministic",
            model="structured-storyboard-agent-v1",
            request_id=None,
            repair_attempts=0,
            validation_report=deterministic_report,
            needs_review=deterministic_report.needs_review,
            diagnostics={"mode": "deterministic", "strict_schema": True},
        )

    prompt = (
        "你是 Storyboard Agent。输入是服务端生成的 ShotSpec[] 草案。"
        "保持 shots 数量、顺序、source、duration_sec 和所有 ID 不变，只提升导演表达的具体性。"
        "不得添加未在草案中出现的人物、道具、地点、对白或剧情事实。"
        "每个镜头只允许一个可执行动作；start_state 必须能从上一镜头 end_state 连续进入；"
        "不得直接跨越同一 axis_id 的左右轴线。"
        "严格返回一个 JSON Object，唯一顶层字段是 shots，shots 的每项必须符合 ShotSpec。"
        "不要 Markdown、解释、注释、自然语言前后缀，也不要输出 Prompt。"
        "输出必须符合以下严格 JSON Schema：\n"
        f"{json.dumps(StoryboardShotPlan.model_json_schema(), ensure_ascii=False)}\n"
        "ShotSpec[] 草案：\n"
        f"{json.dumps(deterministic_plan.model_dump(mode='json'), ensure_ascii=False)}"
    )

    def semantic_validator(value: BaseModel) -> None:
        _validate_agent_plan(value, drafts=drafts)

    try:
        generated = await _ark_json(
            settings,
            prompt=prompt,
            validator=StoryboardShotPlan,
            semantic_validator=semantic_validator,
            max_attempts=2,
            strict_json_schema=True,
        )
    except TextProviderError as exc:
        if exc.code not in {"ARK_TEXT_SCHEMA_INVALID", "STORYBOARD_SHOT_SPEC_INVALID"}:
            raise
        return StoryboardAgentResult(
            shots=deterministic_plan.shots,
            provider="volcengine-ark",
            model=settings.ark_prompt_model,
            request_id=(
                str(exc.details.get("last_request_id"))
                if exc.details.get("last_request_id")
                else None
            ),
            repair_attempts=1,
            validation_report=deterministic_report,
            needs_review=True,
            diagnostics={
                "mode": "fallback_after_repair",
                "strict_schema": True,
                "error_code": exc.code,
                "error_message": str(exc),
                "attempts": exc.details.get("attempts", []),
            },
        )
    plan = StoryboardShotPlan.model_validate(generated.payload)
    report = _validate_agent_plan(plan, drafts=drafts)
    return StoryboardAgentResult(
        shots=plan.shots,
        provider=generated.provider,
        model=generated.model,
        request_id=generated.request_id,
        repair_attempts=generated.repair_attempts,
        validation_report=report,
        needs_review=report.needs_review,
        diagnostics={
            "mode": "provider",
            "strict_schema": True,
            "shot_count": len(plan.shots),
        },
    )
