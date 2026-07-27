import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LineageEdge, ShotSpec, ShotSpecRevision
from app.services.projects import canonical_json


def add_lineage_edge(
    session: Session,
    *,
    project_id: str,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    relation: str,
    evidence: str,
    source_version_id: str | None = None,
    target_version_id: str | None = None,
    inferred: bool = False,
    trace_id: str | None = None,
) -> LineageEdge:
    identity = ":".join(
        (
            project_id,
            source_type,
            source_id,
            source_version_id or "",
            target_type,
            target_id,
            target_version_id or "",
            relation,
        )
    )
    edge_id = str(uuid5(NAMESPACE_URL, f"lineage:{identity}"))
    existing = session.get(LineageEdge, edge_id)
    if existing is not None:
        return existing
    edge = LineageEdge(
        id=edge_id,
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        source_version_id=source_version_id,
        source_version_key=source_version_id or "",
        target_type=target_type,
        target_id=target_id,
        target_version_id=target_version_id,
        target_version_key=target_version_id or "",
        relation=relation,
        evidence=evidence,
        inferred=inferred,
        trace_id=trace_id,
        created_at=datetime.now(UTC),
    )
    session.add(edge)
    return edge


def _json_value(raw: str, fallback: object) -> object:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return fallback


def record_shot_spec_revision(
    session: Session,
    *,
    project_id: str,
    spec: ShotSpec,
    actor: str,
    change_reason: str,
    changes: dict[str, object] | None = None,
    trace_id: str | None = None,
) -> ShotSpecRevision:
    previous = session.scalar(
        select(ShotSpecRevision)
        .where(ShotSpecRevision.shot_spec_id == spec.id)
        .order_by(ShotSpecRevision.version.desc())
        .limit(1)
    )
    version = (
        session.scalar(
            select(func.max(ShotSpecRevision.version)).where(
                ShotSpecRevision.shot_spec_id == spec.id
            )
        )
        or 0
    ) + 1
    snapshot = {
        "description": spec.description,
        "dialogue": spec.dialogue,
        "duration_ms": spec.duration_ms,
        "shot_size": spec.shot_size,
        "camera_movement": spec.camera_movement,
        "character_look_ids": _json_value(spec.character_look_ids_json, []),
        "location_version_id": spec.location_version_id,
        "prop_version_ids": _json_value(spec.prop_version_ids_json, []),
        "prompt": _json_value(spec.prompt_json, {}),
        "status": spec.status,
    }
    revision = ShotSpecRevision(
        id=str(uuid5(NAMESPACE_URL, f"shot-spec-revision:{spec.id}:{version}")),
        project_id=project_id,
        shot_id=spec.shot_id,
        shot_spec_id=spec.id,
        version=version,
        parent_revision_id=previous.id if previous is not None else None,
        source_storyboard_version_id=spec.storyboard_version_id,
        source_script_scene_id=spec.script_scene_id,
        source_script_line_ids_json=spec.script_line_ids_json,
        snapshot_json=canonical_json(snapshot),
        change_json=canonical_json(changes or {}),
        change_reason=change_reason,
        actor=actor,
        content_hash=spec.content_hash,
        trace_id=trace_id,
        created_at=datetime.now(UTC),
    )
    session.add(revision)
    add_lineage_edge(
        session,
        project_id=project_id,
        source_type="ScriptScene",
        source_id=spec.script_scene_id,
        target_type="ShotSpecRevision",
        target_id=revision.id,
        target_version_id=revision.id,
        relation="source_for",
        evidence="shot_spec_revisions.source_script_scene_id",
        trace_id=trace_id,
    )
    add_lineage_edge(
        session,
        project_id=project_id,
        source_type="ShotSpecRevision",
        source_id=revision.id,
        source_version_id=revision.id,
        target_type="Shot",
        target_id=spec.shot_id,
        relation="specifies",
        evidence="shot_spec_revisions.shot_id",
        trace_id=trace_id,
    )
    return revision
