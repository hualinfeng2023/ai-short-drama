import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.shot_spec import ShotSpec


class ShotValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["BLOCKER", "WARNING"]
    field_path: str
    message: str
    repairable: bool
    details: dict[str, object] = Field(default_factory=dict)


class ShotValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    needs_review: bool
    total_duration_sec: float
    issues: list[ShotValidationIssue] = Field(default_factory=list)


_ACTION_SEPARATORS = re.compile(r"[，,；;、]|\b(?:then|while|and then)\b", re.IGNORECASE)
_HIGH_RISK_TOKENS = (
    "镜面",
    "玻璃反射",
    "群像",
    "快速变装",
    "高速追逐",
    "爆炸",
    "水下",
    "复杂手部",
    "多人打斗",
    "文字特写",
)
_LOCATION_PROP_CONFLICT_RULES = (
    {
        "location_tokens": (
            "胚胎储存",
            "胚胎存储",
            "胚胎培养",
            "胚胎库",
            "低温胚胎",
        ),
        "conflicting_tokens": ("婴儿床", "婴儿车", "尿布台"),
        "expected_elements": ("低温胚胎舱", "生命维持管线", "监测终端"),
    },
)


def _issue(
    code: str,
    severity: Literal["BLOCKER", "WARNING"],
    field_path: str,
    message: str,
    *,
    repairable: bool,
    **details: object,
) -> ShotValidationIssue:
    return ShotValidationIssue(
        code=code,
        severity=severity,
        field_path=field_path,
        message=message,
        repairable=repairable,
        details=details,
    )


class ShotValidator:
    def validate_shot(self, shot: ShotSpec, *, index: int = 0) -> list[ShotValidationIssue]:
        prefix = f"shots.{index}"
        issues: list[ShotValidationIssue] = []
        location_text = " ".join(
            (
                shot.visual_content.environment,
                shot.start_state.location,
                shot.start_state.environment_state,
                shot.end_state.location,
                shot.end_state.environment_state,
            )
        )
        visual_text = " ".join(
            (
                shot.visual_content.description,
                shot.visual_content.action,
                shot.start_state.action_state,
                shot.end_state.action_state,
            )
        )
        for rule in _LOCATION_PROP_CONFLICT_RULES:
            matched_locations = [
                token for token in rule["location_tokens"] if token in location_text
            ]
            conflicting_elements = [
                token for token in rule["conflicting_tokens"] if token in visual_text
            ]
            if matched_locations and conflicting_elements:
                issues.append(
                    _issue(
                        "LOCATION_PROP_SEMANTIC_CONFLICT",
                        "BLOCKER",
                        f"{prefix}.visual_content.description",
                        "场景用途与画面道具语义冲突",
                        repairable=True,
                        location=shot.start_state.location,
                        matched_location_tokens=matched_locations,
                        conflicting_elements=conflicting_elements,
                        expected_elements=list(rule["expected_elements"]),
                    )
                )

        action_parts = [
            item.strip()
            for item in _ACTION_SEPARATORS.split(shot.visual_content.action)
            if item.strip()
        ]
        performance_actions = sum(
            max(1, len(_ACTION_SEPARATORS.split(item.action)))
            for item in shot.performance.characters
        )
        action_count = max(len(action_parts), performance_actions)
        allowed_actions = max(1, round(shot.duration_sec / 1.5))
        if action_count > allowed_actions:
            issues.append(
                _issue(
                    "ACTION_COMPLEXITY_EXCEEDED",
                    "BLOCKER",
                    f"{prefix}.visual_content.action",
                    "单镜动作数量超过当前时长可稳定生成的范围",
                    repairable=True,
                    action_count=action_count,
                    allowed_actions=allowed_actions,
                    duration_sec=shot.duration_sec,
                )
            )

        if shot.start_state.location != shot.end_state.location and shot.duration_sec < 2:
            issues.append(
                _issue(
                    "STATE_TRANSITION_TOO_FAST",
                    "BLOCKER",
                    f"{prefix}.end_state.location",
                    "镜头时长不足以完成地点状态变化",
                    repairable=True,
                )
            )
        if (
            shot.start_state.action_state == shot.end_state.action_state
            and shot.visual_content.action not in {"静止", "保持", "无动作"}
        ):
            issues.append(
                _issue(
                    "END_STATE_NOT_ADVANCED",
                    "WARNING",
                    f"{prefix}.end_state.action_state",
                    "动作已经发生，但首尾动作状态没有体现变化",
                    repairable=True,
                )
            )

        state_character_ids = {
            item.character_id
            for item in [*shot.start_state.characters, *shot.end_state.characters]
        }
        missing_characters = set(shot.continuity.character_ids) - state_character_ids
        if missing_characters:
            issues.append(
                _issue(
                    "CHARACTER_STATE_MISSING",
                    "WARNING",
                    f"{prefix}.continuity.character_ids",
                    "连续性角色未在首尾状态中完整描述",
                    repairable=True,
                    character_ids=sorted(missing_characters),
                )
            )

        state_prop_ids = {
            item.prop_id for item in [*shot.start_state.props, *shot.end_state.props]
        }
        missing_props = set(shot.continuity.prop_version_ids) - state_prop_ids
        if missing_props:
            issues.append(
                _issue(
                    "PROP_STATE_MISSING",
                    "WARNING",
                    f"{prefix}.continuity.prop_version_ids",
                    "连续性道具未在首尾状态中完整描述",
                    repairable=True,
                    prop_version_ids=sorted(missing_props),
                )
            )

        risk_text = " ".join(
            (
                shot.visual_content.description,
                shot.visual_content.action,
                shot.technique.notes,
                *shot.technique.vfx,
            )
        )
        detected_risks = [token for token in _HIGH_RISK_TOKENS if token in risk_text]
        if len(shot.continuity.character_ids) > 4:
            detected_risks.append("同镜人物超过 4 人")
        undeclared = [item for item in detected_risks if item not in shot.generation.risk_flags]
        if undeclared:
            issues.append(
                _issue(
                    "GENERATION_RISK_UNDECLARED",
                    "WARNING",
                    f"{prefix}.generation.risk_flags",
                    "镜头包含生成风险，但 generation.risk_flags 未声明",
                    repairable=True,
                    risks=undeclared,
                )
            )
        if len(shot.generation.reference_asset_ids) < len(shot.continuity.character_ids):
            issues.append(
                _issue(
                    "CHARACTER_REFERENCE_COVERAGE",
                    "WARNING",
                    f"{prefix}.generation.reference_asset_ids",
                    "人物参考资产数量少于连续性角色数量",
                    repairable=False,
                    character_count=len(shot.continuity.character_ids),
                    reference_count=len(shot.generation.reference_asset_ids),
                )
            )
        return issues

    def validate_sequence(
        self,
        shots: list[ShotSpec],
        *,
        expected_total_duration_sec: float | None = None,
        expected_scene_durations: dict[int, float] | None = None,
    ) -> ShotValidationReport:
        issues: list[ShotValidationIssue] = []
        for index, shot in enumerate(shots):
            issues.extend(self.validate_shot(shot, index=index))
            if index == 0:
                continue
            previous = shots[index - 1]
            if shot.source.scene_ordinal != previous.source.scene_ordinal:
                continue
            if previous.end_state.location != shot.start_state.location:
                issues.append(
                    _issue(
                        "LOCATION_CONTINUITY_BREAK",
                        "BLOCKER",
                        f"shots.{index}.start_state.location",
                        "同场相邻镜头的地点首尾状态不一致",
                        repairable=True,
                        previous=previous.end_state.location,
                        current=shot.start_state.location,
                    )
                )
            previous_characters = {
                item.character_id: item for item in previous.end_state.characters
            }
            current_characters = {item.character_id: item for item in shot.start_state.characters}
            for character_id in sorted(previous_characters.keys() & current_characters.keys()):
                before = previous_characters[character_id]
                after = current_characters[character_id]
                if before.wardrobe and after.wardrobe and before.wardrobe != after.wardrobe:
                    issues.append(
                        _issue(
                            "CHARACTER_WARDROBE_CONTINUITY_BREAK",
                            "BLOCKER",
                            f"shots.{index}.start_state.characters",
                            "同场相邻镜头的人物服装状态不一致",
                            repairable=True,
                            character_id=character_id,
                            previous=before.wardrobe,
                            current=after.wardrobe,
                        )
                    )
            previous_props = {item.prop_id: item for item in previous.end_state.props}
            current_props = {item.prop_id: item for item in shot.start_state.props}
            for prop_id in sorted(previous_props.keys() & current_props.keys()):
                if previous_props[prop_id].state != current_props[prop_id].state:
                    issues.append(
                        _issue(
                            "PROP_CONTINUITY_BREAK",
                            "BLOCKER",
                            f"shots.{index}.start_state.props",
                            "同场相邻镜头的道具状态不一致",
                            repairable=True,
                            prop_id=prop_id,
                            previous=previous_props[prop_id].state,
                            current=current_props[prop_id].state,
                        )
                    )
            axis_is_same = (
                previous.camera.axis_id
                and previous.camera.axis_id == shot.camera.axis_id
            )
            crossed_axis = {
                previous.camera.axis_side,
                shot.camera.axis_side,
            } == {"LEFT", "RIGHT"}
            if axis_is_same and crossed_axis and shot.camera.shot_size not in {"EWS", "WS"}:
                issues.append(
                    _issue(
                        "CAMERA_AXIS_CROSSED",
                        "BLOCKER",
                        f"shots.{index}.camera.axis_side",
                        "同一镜头轴线从左右侧直接跳转，且没有中性或全景重建镜头",
                        repairable=True,
                        axis_id=shot.camera.axis_id,
                    )
                )

        total_duration = round(sum(item.duration_sec for item in shots), 3)
        if (
            expected_total_duration_sec is not None
            and abs(total_duration - expected_total_duration_sec) > 0.05
        ):
            issues.append(
                _issue(
                    "TOTAL_DURATION_MISMATCH",
                    "BLOCKER",
                    "shots",
                    "镜头总时长与目标时长不一致",
                    repairable=True,
                    actual=total_duration,
                    expected=expected_total_duration_sec,
                )
            )
        if expected_scene_durations:
            actual_by_scene: Counter[int] = Counter()
            for shot in shots:
                actual_by_scene[shot.source.scene_ordinal] += shot.duration_sec
            for scene_ordinal, expected in expected_scene_durations.items():
                actual = round(actual_by_scene[scene_ordinal], 3)
                if abs(actual - expected) <= 0.05:
                    continue
                issues.append(
                    _issue(
                        "SCENE_DURATION_MISMATCH",
                        "BLOCKER",
                        f"scene.{scene_ordinal}",
                        "场景镜头时长之和与剧本场景时长不一致",
                        repairable=True,
                        scene_ordinal=scene_ordinal,
                        actual=actual,
                        expected=expected,
                    )
                )
        has_blocker = any(item.severity == "BLOCKER" for item in issues)
        return ShotValidationReport(
            valid=not has_blocker,
            needs_review=has_blocker,
            total_duration_sec=total_duration,
            issues=issues,
        )
