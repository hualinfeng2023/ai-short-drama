import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GenerationRecord, Job
from app.services.projects import canonical_json, content_hash

TERMINAL_GENERATION_STATUSES = {"SUCCEEDED", "FAILED", "DEGRADED", "CANCELLED"}


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
    return record
