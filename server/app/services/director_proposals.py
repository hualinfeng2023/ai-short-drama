import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    AuditLog,
    ChangeSet,
    GenerationRecord,
    Project,
    Scene,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    Shot,
    ShotSpec,
)
from app.domain.director import DirectorProposalRequest
from app.services.dependency_analysis import analyze_script_scene_dependencies
from app.services.projects import canonical_json, content_hash
from app.services.text_provider import TextProviderError, generate_director_scene_review
from app.services.workspace import project_or_404

logger = logging.getLogger(__name__)


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
    return analyze_script_scene_dependencies(
        session,
        project_id=project_id,
        script_scene_id=script_scene.id,
    )


def director_retry_source(
    session: Session,
    *,
    project_id: str,
    script_scene_id: str,
    generation_record_id: str | None,
) -> GenerationRecord | None:
    if generation_record_id is None:
        return None
    record = session.get(GenerationRecord, generation_record_id)
    if record is None or record.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DIRECTOR_RETRY_SOURCE_NOT_FOUND",
                "message": "指定的 Director 失败记录不存在",
            },
        )
    if record.capability != "DIRECTOR_SCENE_REVIEW" or record.status != "FAILED":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_RETRY_SOURCE_INVALID",
                "message": "Director 重试只能引用失败的场景审查记录",
                "details": {
                    "generation_record_id": record.id,
                    "capability": record.capability,
                    "status": record.status,
                },
            },
        )
    if record.entity_type != "script_scene" or record.entity_id != script_scene_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_RETRY_TARGET_MISMATCH",
                "message": "Director 重试来源与当前 ScriptScene 不一致",
                "details": {
                    "generation_record_id": record.id,
                    "source_entity_type": record.entity_type,
                    "source_entity_id": record.entity_id,
                    "target_script_scene_id": script_scene_id,
                },
            },
        )
    return record


def record_director_generation_failure(
    session: Session,
    *,
    project_id: str,
    script_scene_id: str,
    script_version_id: str,
    actor: str,
    provider: str,
    model: str,
    prompt_source: object,
    error_code: str,
    error_message: str,
    retryable: bool,
    details: dict[str, Any] | None,
    latency_ms: int | None,
    stage: str,
    command_id: str | None = None,
    retry_of_generation_record_id: str | None = None,
) -> GenerationRecord:
    """Persist a failed Director evaluation without creating a domain ChangeSet."""

    normalized_details = details or {}
    diagnostics = normalized_details.get("attempts")
    attempts = diagnostics if isinstance(diagnostics, list) else []
    provider_request_id = normalized_details.get("last_request_id") or normalized_details.get(
        "request_id"
    )
    record_id = str(uuid4())
    now = datetime.now(UTC)
    metadata = {
        "actor": actor,
        "target_script_version_id": script_version_id,
        "target_script_scene_id": script_scene_id,
        "media_generation": False,
        "failure_stage": stage,
        "error": {
            "code": error_code,
            "message": error_message,
            "retryable": retryable,
            "details": normalized_details,
        },
        "attempt_count": len(attempts),
        "repair_attempts": max(0, len(attempts) - 1),
    }
    if command_id:
        metadata["command_id"] = command_id
    if retry_of_generation_record_id:
        metadata["retry_of_generation_record_id"] = retry_of_generation_record_id
    record = GenerationRecord(
        id=record_id,
        project_id=project_id,
        job_id=None,
        entity_type="script_scene",
        entity_id=script_scene_id,
        capability="DIRECTOR_SCENE_REVIEW",
        provider=provider,
        model=model,
        config_version="director-proposal-v2",
        prompt_hash=content_hash(prompt_source),
        seed=None,
        reference_asset_ids_json="[]",
        provider_request_id=(
            str(provider_request_id) if isinstance(provider_request_id, str) else None
        ),
        provider_task_id=None,
        status="FAILED",
        latency_ms=latency_ms,
        input_units=None,
        output_units=None,
        estimated_cost_usd=None,
        output_asset_id=None,
        metadata_json=canonical_json(metadata),
        created_at=now,
        completed_at=now,
    )
    session.add(record)
    session.add(
        AuditLog(
            id=str(uuid4()),
            project_id=project_id,
            actor=actor,
            action="DIRECTOR_SCENE_REVIEW_FAILED",
            entity_type="script_scene",
            entity_id=script_scene_id,
            before_hash=content_hash(prompt_source),
            after_hash=content_hash(metadata),
            trace_id=record_id,
            created_at=now,
        )
    )
    session.flush()
    return record


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
    retry_source = director_retry_source(
        session,
        project_id=project.id,
        script_scene_id=scene.id,
        generation_record_id=request.retry_of_generation_record_id,
    )
    context = _scene_context(session, script, scene)
    prompt_source = {
        "context": context,
        "issue_types": list(request.issue_types),
        "instruction": request.instruction,
        "retry_of_generation_record_id": retry_source.id if retry_source else None,
    }
    started_at = perf_counter()
    try:
        generated = await generate_director_scene_review(
            settings,
            scene_context=context,
            issue_types=list(request.issue_types),
            instruction=request.instruction,
        )
    except TextProviderError as exc:
        latency_ms = max(0, round((perf_counter() - started_at) * 1000))
        try:
            record_director_generation_failure(
                session,
                project_id=project.id,
                script_scene_id=scene.id,
                script_version_id=script.id,
                actor=request.actor,
                provider="volcengine-ark" if settings.ark_api_key else "mock",
                model=(
                    settings.ark_prompt_model
                    if settings.ark_api_key
                    else "deterministic-director-evaluator-v1"
                ),
                prompt_source=prompt_source,
                error_code=exc.code,
                error_message=str(exc),
                retryable=exc.retryable,
                details=exc.details,
                latency_ms=latency_ms,
                stage="PROVIDER_VALIDATION",
                retry_of_generation_record_id=retry_source.id if retry_source else None,
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "Director 失败观测记录写入失败：project_id=%s scene_id=%s",
                project.id,
                scene.id,
            )
        raise HTTPException(
            status_code=503 if exc.retryable else 422,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "details": exc.details,
            },
        ) from exc
    latency_ms = max(0, round((perf_counter() - started_at) * 1000))
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
            "retry_of_generation_record_id": retry_source.id if retry_source else None,
            "instruction": request.instruction or "审查并修复选中场景",
            "review": review,
            "context": context,
            "impact": impact,
            "provider": {
                "provider": generated.provider,
                "model": generated.model,
                "request_id": generated.request_id,
                "repair_attempts": generated.repair_attempts,
                "latency_ms": latency_ms,
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


def _metadata_count(metadata: dict[str, object], key: str, *, default: int = 0) -> int:
    value = metadata.get(key, default)
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def list_director_generation_history(
    session: Session,
    *,
    project_id: str,
    script_scene_id: str | None = None,
) -> list[dict[str, object]]:
    project_or_404(session, project_id)
    query = (
        select(GenerationRecord)
        .where(
            GenerationRecord.project_id == project_id,
            GenerationRecord.capability == "DIRECTOR_SCENE_REVIEW",
        )
        .order_by(GenerationRecord.created_at.desc())
    )
    records = list(session.scalars(query))
    proposal_ids = {
        record.entity_id
        for record in records
        if record.entity_type == "director_proposal"
    }
    proposals_by_id = {
        item.id: item
        for item in session.scalars(
            select(ChangeSet).where(
                ChangeSet.project_id == project_id,
                ChangeSet.id.in_(proposal_ids),
            )
        )
    } if proposal_ids else {}
    history: list[dict[str, object]] = []
    for record in records:
        try:
            metadata = json.loads(record.metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        metadata = metadata if isinstance(metadata, dict) else {}
        target_script_scene_id = (
            record.entity_id
            if record.entity_type == "script_scene"
            else metadata.get("target_script_scene_id")
        )
        if not isinstance(target_script_scene_id, str):
            continue
        if script_scene_id is not None and target_script_scene_id != script_scene_id:
            continue
        error = metadata.get("error")
        error = error if isinstance(error, dict) else {}
        proposal = proposals_by_id.get(record.entity_id)
        proposal_payload: dict[str, object] = {}
        if proposal is not None:
            try:
                impact = json.loads(proposal.impact_json)
            except json.JSONDecodeError:
                impact = {}
            if isinstance(impact, dict) and isinstance(impact.get("proposal"), dict):
                proposal_payload = impact["proposal"]
        repair_attempts = _metadata_count(metadata, "repair_attempts")
        attempt_count = _metadata_count(
            metadata,
            "attempt_count",
            default=repair_attempts + 1 if record.status == "SUCCEEDED" else 0,
        )
        history.append(
            {
                "generation_record_id": record.id,
                "script_scene_id": target_script_scene_id,
                "script_version_id": metadata.get("target_script_version_id"),
                "status": record.status,
                "provider": record.provider,
                "model": record.model,
                "provider_request_id": record.provider_request_id,
                "latency_ms": record.latency_ms,
                "estimated_cost_usd": record.estimated_cost_usd,
                "attempt_count": attempt_count,
                "repair_attempts": repair_attempts,
                "failure_stage": metadata.get("failure_stage"),
                "error_code": error.get("code"),
                "error_message": error.get("message"),
                "retryable": bool(error.get("retryable", False)),
                "retry_of_generation_record_id": metadata.get(
                    "retry_of_generation_record_id"
                ),
                "proposal_id": proposal.id if proposal is not None else None,
                "proposal_status": proposal.status if proposal is not None else None,
                "issue_type": proposal_payload.get("issue_type"),
                "created_at": record.created_at,
                "completed_at": record.completed_at,
            }
        )
    return history


def list_director_generation_failures(
    session: Session,
    *,
    project_id: str,
    script_scene_id: str | None = None,
) -> list[dict[str, object]]:
    return [
        item
        for item in list_director_generation_history(
            session,
            project_id=project_id,
            script_scene_id=script_scene_id,
        )
        if item["status"] == "FAILED"
    ]
