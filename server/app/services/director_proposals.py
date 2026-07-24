import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    ChangeSet,
    Project,
    Scene,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    Shot,
    ShotSpec,
    Take,
    TimelineClip,
    TimelineItem,
)
from app.domain.director import DirectorProposalRequest
from app.services.projects import content_hash
from app.services.text_provider import TextProviderError, generate_director_scene_review
from app.services.workspace import project_or_404


@dataclass(frozen=True)
class DirectorProposalDraft:
    target_object_id: str
    target_version_id: str
    target_hash: str
    payload: dict[str, object]


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _resolve_script_scene(
    session: Session,
    *,
    project: Project,
    target_type: str,
    target_id: str,
) -> tuple[ScriptVersion, ScriptScene]:
    if target_type == "SCRIPT_SCENE":
        script_scene = session.get(ScriptScene, target_id)
    else:
        scene = session.get(Scene, target_id)
        if scene is None:
            raise _error(404, "DIRECTOR_TARGET_NOT_FOUND", "目标 Scene 不存在")
        spec = session.scalar(
            select(ShotSpec)
            .join(Shot, ShotSpec.shot_id == Shot.id)
            .where(Shot.scene_id == scene.id)
            .order_by(ShotSpec.ordinal)
            .limit(1)
        )
        script_scene = session.get(ScriptScene, spec.script_scene_id) if spec is not None else None
        if script_scene is None:
            raise _error(
                409,
                "SCENE_SCRIPT_LINEAGE_MISSING",
                "该生产 Scene 尚未通过 ShotSpec 映射到 ScriptScene",
            )
    script = (
        session.get(ScriptVersion, script_scene.script_version_id)
        if script_scene is not None
        else None
    )
    if script is None or script_scene is None or script.project_id != project.id:
        raise _error(404, "DIRECTOR_TARGET_NOT_FOUND", "目标场景不属于当前项目")
    return script, script_scene


def _scene_context(session: Session, script: ScriptVersion, scene: ScriptScene) -> dict[str, Any]:
    lines = list(
        session.scalars(
            select(ScriptLine)
            .where(ScriptLine.script_scene_id == scene.id)
            .order_by(ScriptLine.ordinal)
        )
    )
    return {
        "script_id": script.id,
        "script_version": script.version,
        "id": scene.id,
        "ordinal": scene.ordinal,
        "heading": scene.heading,
        "location": scene.location,
        "time_of_day": scene.time_of_day,
        "purpose": scene.purpose,
        "emotion": scene.emotion,
        "duration_ms": scene.duration_ms,
        "lines": [
            {
                "id": line.id,
                "ordinal": line.ordinal,
                "speaker_key": line.speaker_key,
                "text": line.text,
                "emotion": line.emotion,
                "speech_rate": line.speech_rate,
                "pause_after_ms": line.pause_after_ms,
                "estimated_duration_ms": line.estimated_duration_ms,
            }
            for line in lines
        ],
    }


def _impact(
    session: Session,
    *,
    project_id: str,
    script_scene: ScriptScene,
) -> dict[str, object]:
    specs = list(
        session.scalars(select(ShotSpec).where(ShotSpec.script_scene_id == script_scene.id))
    )
    shot_ids = {item.shot_id for item in specs}
    take_rows = (
        list(session.scalars(select(Take).where(Take.shot_id.in_(shot_ids)))) if shot_ids else []
    )
    take_ids = {item.id for item in take_rows}
    timeline_item_ids = (
        set(
            session.scalars(
                select(TimelineItem.id).where(
                    (TimelineItem.shot_id.in_(shot_ids)) | (TimelineItem.take_id.in_(take_ids))
                )
            )
        )
        if shot_ids or take_ids
        else set()
    )
    timeline_clip_ids = (
        set(
            session.scalars(
                select(TimelineClip.id).where(
                    TimelineClip.project_id == project_id,
                    (
                        (TimelineClip.source_entity_type == "SHOT")
                        & TimelineClip.source_entity_id.in_(shot_ids)
                    )
                    | (
                        (TimelineClip.source_entity_type == "TAKE")
                        & TimelineClip.source_entity_id.in_(take_ids)
                    ),
                )
            )
        )
        if shot_ids or take_ids
        else set()
    )
    preserved = list(
        session.scalars(
            select(Take).where(
                Take.shot_id.not_in(shot_ids),
                Take.approval == "APPROVED",
            )
        )
    )
    return {
        "affected_objects": [
            *[{"type": "Shot", "id": value, "next_status": "SUSPECT"} for value in shot_ids],
            *[
                {
                    "type": "Take",
                    "id": value.id,
                    "next_status": "SUSPECT",
                    "approval_preserved": value.approval == "APPROVED",
                }
                for value in take_rows
            ],
            *[
                {"type": "TimelineClip", "id": value, "next_status": "SUSPECT"}
                for value in timeline_item_ids | timeline_clip_ids
            ],
        ],
        "preserved_objects": [
            {
                "type": "Take",
                "id": item.id,
                "approval": item.approval,
                "state_hash": content_hash(
                    {
                        "status": item.status,
                        "approval": item.approval,
                        "asset_id": item.asset_id,
                        "is_current": item.is_current,
                    }
                ),
            }
            for item in preserved
        ],
        "shot_ids": sorted(shot_ids),
        "take_ids": sorted(take_ids),
        "timeline_item_ids": sorted(timeline_item_ids),
        "timeline_clip_ids": sorted(timeline_clip_ids),
    }


async def prepare_director_proposal(
    session: Session,
    settings: Settings,
    *,
    project_id: str,
    request: DirectorProposalRequest,
) -> DirectorProposalDraft:
    project = project_or_404(session, project_id)
    if project.lock_version != request.expected_version:
        raise _error(409, "VERSION_CONFLICT", "项目已发生变化，请刷新后重新审查")
    script, scene = _resolve_script_scene(
        session,
        project=project,
        target_type=request.target_type,
        target_id=request.target_id,
    )
    context = _scene_context(session, script, scene)
    try:
        generated = await generate_director_scene_review(
            settings,
            scene_context=context,
            issue_types=list(request.issue_types),
            instruction=request.instruction,
        )
    except TextProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 422,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "details": exc.details,
            },
        ) from exc
    review = generated.payload
    impact = _impact(session, project_id=project.id, script_scene=scene)
    return DirectorProposalDraft(
        target_object_id=scene.id,
        target_version_id=script.id,
        target_hash=script.content_hash,
        payload={
            "requested_by": request.actor,
            "target_type": request.target_type,
            "requested_target_id": request.target_id,
            "script_scene_id": scene.id,
            "instruction": request.instruction or "审查并修复选中场景",
            "review": review,
            "context": context,
            "impact": impact,
            "provider": {
                "provider": generated.provider,
                "model": generated.model,
                "request_id": generated.request_id,
                "repair_attempts": generated.repair_attempts,
            },
        },
    )


def director_proposal_to_read(change_set: ChangeSet) -> dict[str, object]:
    impact = json.loads(change_set.impact_json)
    return {
        **impact["proposal"],
        "proposal_id": change_set.id,
        "project_id": change_set.project_id,
        "status": change_set.status,
        "created_at": change_set.created_at,
        "result_script_version_id": impact.get("result_script_version_id"),
        "rollback_script_version_id": impact.get("rollback_script_version_id"),
        "comparison": impact.get("comparison"),
        "invalidated": impact.get("invalidated", []),
        "approval_result": impact.get("approval_result"),
    }


def director_proposal_or_404(session: Session, proposal_id: str) -> dict[str, object]:
    change_set = session.get(ChangeSet, proposal_id)
    if change_set is None:
        raise _error(404, "DIRECTOR_PROPOSAL_NOT_FOUND", "Director Proposal 不存在")
    impact = json.loads(change_set.impact_json)
    if "proposal" not in impact:
        raise _error(404, "DIRECTOR_PROPOSAL_NOT_FOUND", "该 ChangeSet 不是 Director Proposal")
    return director_proposal_to_read(change_set)


def list_director_proposals(session: Session, *, project_id: str) -> list[dict[str, object]]:
    project_or_404(session, project_id)
    change_sets = list(
        session.scalars(
            select(ChangeSet)
            .where(ChangeSet.project_id == project_id)
            .order_by(ChangeSet.created_at.desc())
        )
    )
    proposals: list[dict[str, object]] = []
    for change_set in change_sets:
        try:
            impact = json.loads(change_set.impact_json)
        except json.JSONDecodeError:
            continue
        if isinstance(impact, dict) and "proposal" in impact:
            proposals.append(director_proposal_to_read(change_set))
    return proposals
