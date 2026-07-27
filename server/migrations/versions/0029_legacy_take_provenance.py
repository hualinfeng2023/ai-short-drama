"""Backfill legacy Take generation records and structured generation context.

Revision ID: 0029_legacy_take_provenance
Revises: 0028_provenance_traceability
Create Date: 2026-07-27
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "0029_legacy_take_provenance"
down_revision: str | None = "0028_provenance_traceability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PARAMETER_KEYS = {
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


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json(raw: object, fallback: object) -> object:
    if not isinstance(raw, str):
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _uuid(*parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(str(part or "") for part in parts)))


def _backfill_generation_context() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT gr.id, gr.job_id, gr.input_snapshot_json, gr.parameters_json, "
            "gr.rules_json, gr.director_intent_json, j.input_json "
            "FROM generation_records gr LEFT JOIN jobs j ON j.id = gr.job_id"
        )
    ).mappings()
    for row in rows:
        payload = _json(row["input_snapshot_json"], {})
        if not isinstance(payload, dict) or not payload:
            payload = _json(row["input_json"], {})
        if not isinstance(payload, dict) or not payload:
            payload = {
                "legacy_unresolved": True,
                "reason": "原始任务输入不可用",
            }
        values: dict[str, object] = {"input_snapshot_json": _canonical(payload)}
        for column, keys, label in (
            ("parameters_json", PARAMETER_KEYS, "parameters"),
            ("rules_json", RULE_KEYS, "rules"),
            ("director_intent_json", DIRECTOR_INTENT_KEYS, "director_intent"),
        ):
            current = _json(row[column], {})
            if not isinstance(current, dict) or not current:
                extracted = {key: payload[key] for key in sorted(keys) if key in payload}
                values[column] = _canonical(
                    extracted
                    or {
                        "captured": False,
                        "reason": f"历史记录未保存 {label}",
                    }
                )
        for column in ("parameters_json", "rules_json", "director_intent_json"):
            values.setdefault(column, None)
        bind.execute(
            sa.text(
                "UPDATE generation_records SET input_snapshot_json=:input_snapshot_json, "
                "parameters_json=COALESCE(:parameters_json, parameters_json), "
                "rules_json=COALESCE(:rules_json, rules_json), "
                "director_intent_json=COALESCE(:director_intent_json, director_intent_json) "
                "WHERE id=:id"
            ),
            {"id": row["id"], **values},
        )


def _backfill_legacy_takes() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    rows = bind.execute(
        sa.text(
            "SELECT e.project_id, t.*, a.provider, a.status AS asset_status, "
            "a.metadata_json AS asset_metadata_json "
            "FROM takes t JOIN assets a ON a.id=t.asset_id "
            "JOIN shots s ON s.id=t.shot_id "
            "JOIN scenes sc ON sc.id=s.scene_id "
            "JOIN episodes e ON e.id=sc.episode_id "
            "WHERE t.generation_record_id IS NULL"
        )
    ).mappings()
    for row in rows:
        metadata = _json(row["asset_metadata_json"], {})
        generation = metadata.get("generation", {}) if isinstance(metadata, dict) else {}
        if not isinstance(generation, dict):
            generation = {}
        references = _json(row["identity_reference_asset_ids_json"], [])
        if not isinstance(references, list):
            references = []
        snapshot = {
            "legacy_unresolved": True,
            "take_id": row["id"],
            "take_kind": row["kind"],
            "take_version": row["version"],
            "shot_id": row["shot_id"],
            "asset_id": row["asset_id"],
        }
        record_id = _uuid("legacy-take-generation", row["id"])
        prompt_hash = hashlib.sha256(_canonical(snapshot).encode()).hexdigest()
        bind.execute(
            sa.text(
                "INSERT INTO generation_records ("
                "id, project_id, job_id, entity_type, entity_id, capability, provider, model, "
                "config_version, prompt_hash, seed, reference_asset_ids_json, "
                "input_snapshot_json, parameters_json, rules_json, director_intent_json, "
                "provider_request_id, provider_task_id, status, latency_ms, input_units, "
                "output_units, estimated_cost_usd, output_asset_id, metadata_json, "
                "created_at, completed_at"
                ") VALUES ("
                ":id, :project_id, NULL, 'take', :take_id, 'LEGACY_TAKE_IMPORT', "
                ":provider, :model, 'legacy-backfill-v1', :prompt_hash, :seed, :refs, "
                ":snapshot, :parameters, :rules, :intent, NULL, NULL, :status, NULL, NULL, "
                "NULL, NULL, :asset_id, :metadata, :created_at, :completed_at)"
            ),
            {
                "id": record_id,
                "project_id": row["project_id"],
                "take_id": row["id"],
                "provider": row["provider"] or "unknown",
                "model": generation.get("model") or "unknown",
                "prompt_hash": prompt_hash,
                "seed": str(generation["seed"]) if generation.get("seed") is not None else None,
                "refs": _canonical(references),
                "snapshot": _canonical(snapshot),
                "parameters": _canonical(
                    {
                        key: generation[key]
                        for key in sorted(PARAMETER_KEYS)
                        if key in generation
                    }
                    or {"captured": False, "reason": "历史 Take 参数不可恢复"}
                ),
                "rules": _canonical(
                    {"captured": False, "reason": "历史 Take 规则不可恢复"}
                ),
                "intent": _canonical(
                    {"captured": False, "reason": "历史 Take 导演意图不可恢复"}
                ),
                "status": "SUCCEEDED" if row["asset_status"] == "READY" else "DEGRADED",
                "asset_id": row["asset_id"],
                "metadata": _canonical(
                    {
                        "legacy_backfill": True,
                        "provenance_completeness": "PARTIAL",
                        "unresolved_fields": ["prompt", "rules", "director_intent"],
                    }
                ),
                "created_at": row["created_at"] or now,
                "completed_at": row["created_at"] or now,
            },
        )
        bind.execute(
            sa.text("UPDATE takes SET generation_record_id=:record_id WHERE id=:take_id"),
            {"record_id": record_id, "take_id": row["id"]},
        )
        for source_type, source_id, target_type, target_id, relation, evidence in (
            ("GenerationRecord", record_id, "Take", row["id"], "generates", "legacy backfill"),
            (
                "GenerationRecord",
                record_id,
                "Asset",
                row["asset_id"],
                "produces",
                "generation_records.output_asset_id",
            ),
        ):
            bind.execute(
                sa.text(
                    "INSERT OR IGNORE INTO lineage_edges ("
                    "id, project_id, source_type, source_id, source_version_id, "
                    "source_version_key, target_type, target_id, target_version_id, "
                    "target_version_key, relation, evidence, inferred, trace_id, created_at"
                    ") VALUES ("
                    ":id, :project_id, :source_type, :source_id, NULL, '', :target_type, "
                    ":target_id, NULL, '', :relation, :evidence, 0, NULL, :created_at)"
                ),
                {
                    "id": _uuid(
                        "lineage",
                        row["project_id"],
                        source_type,
                        source_id,
                        target_type,
                        target_id,
                        relation,
                    ),
                    "project_id": row["project_id"],
                    "source_type": source_type,
                    "source_id": source_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "relation": relation,
                    "evidence": evidence,
                    "created_at": now,
                },
            )


def upgrade() -> None:
    _backfill_generation_context()
    _backfill_legacy_takes()


def downgrade() -> None:
    # Legacy provenance is intentionally retained.
    pass
