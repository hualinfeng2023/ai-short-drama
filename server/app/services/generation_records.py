import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GenerationRecord, Job
from app.services.projects import canonical_json, content_hash
from app.services.provenance import add_lineage_edge

TERMINAL_GENERATION_STATUSES = {"SUCCEEDED", "FAILED", "DEGRADED", "CANCELLED"}
GENERATION_PARAMETER_KEYS = {
    "aspect_ratio",
    "camera_fixed",
    "duration",
    "duration_sec",
    "fps",
    "height",
    "model",
    "resolution",
    "seed",
    "size",
    "steps",
    "strength",
    "temperature",
    "watermark",
    "width",
}
DIRECTOR_INTENT_KEYS = {
    "custom_prompt",
    "prompt_snapshot",
    "refinement_note",
    "shot_size",
    "camera_movement",
    "location",
    "time_of_day",
}
RULE_KEYS = {
    "content_avoidances",
    "family_constraint_version_id",
    "quality_context",
    "rules",
    "ruleset",
    "safety_rules",
}


def ensure_generation_record(
    session: Session,
    *,
    job: Job,
    capability: str,
    provider: str,
    model: str,
    config_version: str,
    prompt: str | None,
    seed: object | None,
    reference_asset_ids: Sequence[str],
    output_asset_id: str | None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    provider_request_id: str | None = None,
    provider_task_id: str | None = None,
    status: str = "SUCCEEDED",
    latency_ms: int | None = None,
    input_units: float | None = None,
    output_units: float | None = None,
    estimated_cost_usd: float | None = None,
    metadata: dict[str, object] | None = None,
    input_snapshot: dict[str, object] | None = None,
    parameters: dict[str, object] | None = None,
    rules: dict[str, object] | None = None,
    director_intent: dict[str, object] | None = None,
) -> GenerationRecord:
    """Create one idempotent generation trace for a job capability and output entity."""

    resolved_entity_type = entity_type or job.entity_type
    resolved_entity_id = entity_id or job.entity_id
    existing = session.scalar(
        select(GenerationRecord).where(
            GenerationRecord.job_id == job.id,
            GenerationRecord.capability == capability,
            GenerationRecord.entity_type == resolved_entity_type,
            GenerationRecord.entity_id == resolved_entity_id,
        )
    )
    if existing is not None:
        return existing

    try:
        job_input = json.loads(job.input_json or "{}")
    except json.JSONDecodeError:
        job_input = {}
    if not isinstance(job_input, dict):
        job_input = {}
    resolved_input_snapshot = input_snapshot if input_snapshot is not None else job_input
    resolved_parameters = parameters if parameters is not None else {
        key: job_input[key] for key in sorted(GENERATION_PARAMETER_KEYS) if key in job_input
    }
    resolved_rules = rules if rules is not None else {
        key: job_input[key] for key in sorted(RULE_KEYS) if key in job_input
    }
    resolved_director_intent = director_intent if director_intent is not None else {
        key: job_input[key] for key in sorted(DIRECTOR_INTENT_KEYS) if key in job_input
    }
    command_id = job_input.get("command_id") if isinstance(job_input, dict) else None
    trace_metadata = {
        **(metadata or {}),
        "trace_id": job.trace_id,
        "job_type": job.job_type,
        "job_request_hash": job.request_hash,
        "job_idempotency_key": job.idempotency_key,
    }
    if isinstance(command_id, str) and command_id:
        trace_metadata["command_id"] = command_id

    now = datetime.now(UTC)
    record = GenerationRecord(
        id=str(uuid4()),
        project_id=job.project_id,
        job_id=job.id,
        entity_type=resolved_entity_type,
        entity_id=resolved_entity_id,
        capability=capability,
        provider=provider,
        model=model,
        config_version=config_version,
        prompt_hash=content_hash(prompt or job.input_json or ""),
        seed=str(seed) if seed is not None else None,
        reference_asset_ids_json=canonical_json(list(reference_asset_ids)),
        input_snapshot_json=canonical_json(resolved_input_snapshot),
        parameters_json=canonical_json(resolved_parameters),
        rules_json=canonical_json(resolved_rules),
        director_intent_json=canonical_json(resolved_director_intent),
        provider_request_id=provider_request_id,
        provider_task_id=provider_task_id,
        status=status,
        latency_ms=latency_ms,
        input_units=input_units,
        output_units=output_units,
        estimated_cost_usd=estimated_cost_usd,
        output_asset_id=output_asset_id,
        metadata_json=canonical_json(trace_metadata),
        created_at=now,
        completed_at=now if status in TERMINAL_GENERATION_STATUSES else None,
    )
    session.add(record)
    session.flush()
    add_lineage_edge(
        session,
        project_id=job.project_id,
        source_type="Job",
        source_id=job.id,
        target_type="GenerationRecord",
        target_id=record.id,
        relation="materializes",
        evidence="generation_records.job_id",
        trace_id=job.trace_id,
    )
    for reference_asset_id in reference_asset_ids:
        add_lineage_edge(
            session,
            project_id=job.project_id,
            source_type="Asset",
            source_id=reference_asset_id,
            target_type="GenerationRecord",
            target_id=record.id,
            relation="reference_for",
            evidence="generation_records.reference_asset_ids_json",
            trace_id=job.trace_id,
        )
    if output_asset_id:
        add_lineage_edge(
            session,
            project_id=job.project_id,
            source_type="GenerationRecord",
            source_id=record.id,
            target_type="Asset",
            target_id=output_asset_id,
            relation="produces",
            evidence="generation_records.output_asset_id",
            trace_id=job.trace_id,
        )
    add_lineage_edge(
        session,
        project_id=job.project_id,
        source_type="GenerationRecord",
        source_id=record.id,
        target_type=resolved_entity_type,
        target_id=resolved_entity_id,
        relation="generates",
        evidence="generation_records.entity_type/entity_id",
        trace_id=job.trace_id,
    )
    return record
