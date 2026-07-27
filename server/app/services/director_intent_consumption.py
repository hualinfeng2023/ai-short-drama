import json
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChangeSet, ScriptVersion
from app.domain.director_intent import (
    DirectorIntent,
    DirectorIntentChangePreview,
    DirectorIntentConsumer,
)
from app.services.projects import canonical_json, content_hash

ConsumptionStatus = Literal["INHERITED", "STALE", "BLOCKED"]


@dataclass(frozen=True)
class ConfirmedDirectorIntent:
    change_set_id: str
    result_script_version_id: str
    scene_ordinal: int
    intent: DirectorIntent

    def snapshot_for(self, consumer: DirectorIntentConsumer) -> dict[str, object]:
        target = next(
            item for item in self.intent.inheritance_targets if item.consumer == consumer
        )
        inherited_fields = set(target.inherited_fields)
        directives = [
            item.model_dump(mode="json")
            for item in self.intent.directives
            if item.channel in inherited_fields
        ]
        core: dict[str, object] = {
            "schema_version": "director-intent-consumption-v1",
            "consumer": consumer,
            "intent_id": self.intent.intent_id,
            "intent_version": self.intent.intent_version,
            "source_change_set_id": self.change_set_id,
            "source_script_version_id": self.result_script_version_id,
            "source_fingerprint": self.intent.scope.context_fingerprint,
            "scene_ordinal": self.scene_ordinal,
            "time_range": (
                self.intent.scope.time_range.model_dump(mode="json")
                if self.intent.scope.time_range is not None
                else None
            ),
            "inherited_fields": list(target.inherited_fields),
            "directives": directives,
            "rationale": self.intent.rationale,
            "overall_confidence": self.intent.overall_confidence,
            "preserves": [
                "已锁定角色身份、外观与关系",
                "Story Bible 世界规则和连续性规则",
                "作用范围外的场景、镜头与时间段",
            ],
        }
        return {**core, "receipt_hash": content_hash(core)}


def _script_lineage(session: Session, script: ScriptVersion) -> list[str]:
    lineage: list[str] = []
    current: ScriptVersion | None = script
    visited: set[str] = set()
    while current is not None and current.id not in visited:
        lineage.append(current.id)
        visited.add(current.id)
        current = (
            session.get(ScriptVersion, current.parent_version_id)
            if current.parent_version_id
            else None
        )
    return lineage


def confirmed_director_intents_by_scene(
    session: Session,
    *,
    script: ScriptVersion,
) -> dict[int, ConfirmedDirectorIntent]:
    """Resolve the nearest confirmed intent on the approved script lineage per scene."""

    lineage = _script_lineage(session, script)
    lineage_rank = {script_id: index for index, script_id in enumerate(lineage)}
    candidates: list[tuple[int, ChangeSet, dict[str, Any], DirectorIntent]] = []
    for change_set in session.scalars(
        select(ChangeSet)
        .where(ChangeSet.project_id == script.project_id)
        .order_by(ChangeSet.created_at.desc())
    ):
        try:
            impact = json.loads(change_set.impact_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(impact, dict):
            continue
        result_script_id = impact.get("result_script_version_id")
        proposal = impact.get("proposal")
        if (
            not isinstance(result_script_id, str)
            or result_script_id not in lineage_rank
            or not isinstance(proposal, dict)
        ):
            continue
        preview_payload = proposal.get("director_intent_preview")
        if not isinstance(preview_payload, dict):
            continue
        try:
            preview = DirectorIntentChangePreview.model_validate(preview_payload)
        except ValueError:
            continue
        if preview.intent.state != "CONFIRMED":
            continue
        scene_ordinal = proposal.get("scene_ordinal")
        if not isinstance(scene_ordinal, int) or scene_ordinal < 1:
            continue
        candidates.append(
            (
                lineage_rank[result_script_id],
                change_set,
                proposal,
                preview.intent,
            )
        )

    resolved: dict[int, ConfirmedDirectorIntent] = {}
    for _, change_set, proposal, intent in sorted(
        candidates,
        key=lambda item: (item[0], -item[1].created_at.timestamp()),
    ):
        scene_ordinal = int(proposal["scene_ordinal"])
        if scene_ordinal in resolved:
            continue
        impact = json.loads(change_set.impact_json)
        resolved[scene_ordinal] = ConfirmedDirectorIntent(
            change_set_id=change_set.id,
            result_script_version_id=str(impact["result_script_version_id"]),
            scene_ordinal=scene_ordinal,
            intent=intent,
        )
    return resolved


def director_intent_prompt_block(snapshot: dict[str, object]) -> str:
    directives = snapshot.get("directives")
    if not isinstance(directives, list):
        return ""
    lines = [
        (
            "[已确认导演意图 "
            f"v{snapshot.get('intent_version')} · 场景 {snapshot.get('scene_ordinal')}]"
        )
    ]
    for item in directives:
        if not isinstance(item, dict) or item.get("status") != "CHANGE":
            continue
        lines.append(
            f"- {item.get('channel')}：{item.get('instruction')} "
            f"可观察结果：{item.get('observable_effect')}"
        )
    lines.append("- 硬约束：不得覆盖已锁定角色、世界规则或作用范围外内容。")
    return "\n".join(lines)


def update_director_intent_inheritance_receipt(
    session: Session,
    *,
    intent: ConfirmedDirectorIntent,
    consumer: DirectorIntentConsumer,
    status: ConsumptionStatus,
    output_version: str | None,
    evidence: dict[str, object],
) -> None:
    change_set = session.get(ChangeSet, intent.change_set_id)
    if change_set is None:
        return
    try:
        impact = json.loads(change_set.impact_json)
    except (json.JSONDecodeError, TypeError):
        return
    proposal = impact.get("proposal")
    if not isinstance(proposal, dict):
        return
    inheritance = proposal.get("director_intent_inheritance")
    entries = (
        [dict(item) for item in inheritance if isinstance(item, dict)]
        if isinstance(inheritance, list)
        else []
    )
    receipt: dict[str, object] = {
        "consumer": consumer,
        "status": status,
        "intent_version": intent.intent.intent_version,
        "source_fingerprint": intent.intent.scope.context_fingerprint,
        "applied_range": (
            intent.intent.scope.time_range.model_dump(mode="json")
            if intent.intent.scope.time_range is not None
            else None
        ),
        "output_version": output_version,
        "evidence": evidence,
    }
    replacement_index = next(
        (
            index
            for index, item in enumerate(entries)
            if item.get("consumer") == consumer
        ),
        None,
    )
    if replacement_index is None:
        entries.append(receipt)
    else:
        entries[replacement_index] = receipt
    proposal["director_intent_inheritance"] = entries
    impact["proposal"] = proposal
    change_set.impact_json = canonical_json(impact)
