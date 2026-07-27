"""Persist complete generation snapshots and object traceability.

Revision ID: 0028_provenance_traceability
Revises: 0027_project_thumbnails
Create Date: 2026-07-27
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "0028_provenance_traceability"
down_revision: str | None = "0027_project_thumbnails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_generation_columns() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("generation_records")}
    for name in (
        "input_snapshot_json",
        "parameters_json",
        "rules_json",
        "director_intent_json",
    ):
        if name not in columns:
            op.add_column(
                "generation_records",
                sa.Column(name, sa.Text(), nullable=False, server_default="{}"),
            )

    jobs = sa.table(
        "jobs",
        sa.column("id", sa.String(36)),
        sa.column("input_json", sa.Text()),
    )
    records = sa.table(
        "generation_records",
        sa.column("id", sa.String(36)),
        sa.column("job_id", sa.String(36)),
        sa.column("reference_asset_ids_json", sa.Text()),
        sa.column("input_snapshot_json", sa.Text()),
    )
    job_inputs = {
        row.id: row.input_json
        for row in bind.execute(sa.select(jobs.c.id, jobs.c.input_json))
    }
    for row in bind.execute(
        sa.select(
            records.c.id,
            records.c.job_id,
            records.c.reference_asset_ids_json,
            records.c.input_snapshot_json,
        )
    ):
        raw_input = job_inputs.get(row.job_id) or "{}"
        values: dict[str, object] = {}
        if not row.input_snapshot_json or row.input_snapshot_json == "{}":
            values["input_snapshot_json"] = raw_input
        try:
            payload = json.loads(raw_input)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        try:
            references = json.loads(row.reference_asset_ids_json or "[]")
        except (TypeError, json.JSONDecodeError):
            references = []
        if isinstance(payload, dict) and not references:
            singular = payload.get("reference_asset_id")
            plural = payload.get("reference_asset_ids")
            if isinstance(plural, list):
                references = [item for item in plural if isinstance(item, str)]
            elif isinstance(singular, str):
                references = [singular]
            if references:
                values["reference_asset_ids_json"] = json.dumps(
                    references, ensure_ascii=False, separators=(",", ":")
                )
        if values:
            bind.execute(records.update().where(records.c.id == row.id).values(**values))


def _add_audit_columns() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("audit_log")
    }
    definitions = (
        ("target_version_id", sa.String(36), None),
        ("actor_type", sa.String(24), "USER"),
        ("command_payload_json", sa.Text(), "{}"),
        ("result_snapshot_json", sa.Text(), "{}"),
        ("rules_json", sa.Text(), "{}"),
        ("director_intent_json", sa.Text(), "{}"),
        ("rejection_reasons_json", sa.Text(), "[]"),
    )
    for name, column_type, default in definitions:
        if name in columns:
            continue
        op.add_column(
            "audit_log",
            sa.Column(
                name,
                column_type,
                nullable=default is None,
                server_default=default,
            ),
        )


def _add_review_columns() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("review_records")
    }
    if "entity_version_id" not in columns:
        op.add_column(
            "review_records",
            sa.Column("entity_version_id", sa.String(36), nullable=True),
        )
    for name in ("rules_json", "director_intent_json"):
        if name not in columns:
            op.add_column(
                "review_records",
                sa.Column(name, sa.Text(), nullable=False, server_default="{}"),
            )


def _create_shot_spec_revisions() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "shot_spec_revisions" not in tables:
        op.create_table(
            "shot_spec_revisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("shot_id", sa.String(36), sa.ForeignKey("shots.id"), nullable=False),
            sa.Column("shot_spec_id", sa.String(36), sa.ForeignKey("shot_specs.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column(
                "parent_revision_id",
                sa.String(36),
                sa.ForeignKey("shot_spec_revisions.id"),
                nullable=True,
            ),
            sa.Column("source_storyboard_version_id", sa.String(36), nullable=False),
            sa.Column("source_script_scene_id", sa.String(36), nullable=False),
            sa.Column("source_script_line_ids_json", sa.Text(), nullable=False),
            sa.Column("snapshot_json", sa.Text(), nullable=False),
            sa.Column("change_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("change_reason", sa.Text(), nullable=False),
            sa.Column("actor", sa.String(80), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("trace_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("shot_spec_id", "version"),
        )
        op.create_index(
            "ix_shot_spec_revisions_project_id",
            "shot_spec_revisions",
            ["project_id"],
        )
        op.create_index(
            "ix_shot_spec_revisions_shot_id",
            "shot_spec_revisions",
            ["shot_id"],
        )
        op.create_index(
            "ix_shot_spec_revisions_shot_spec_id",
            "shot_spec_revisions",
            ["shot_spec_id"],
        )


def _create_lineage_edges() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "lineage_edges" in tables:
        return
    op.create_table(
        "lineage_edges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_version_id", sa.String(36), nullable=True),
        sa.Column("source_version_key", sa.String(36), nullable=False, server_default=""),
        sa.Column("target_type", sa.String(48), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("target_version_id", sa.String(36), nullable=True),
        sa.Column("target_version_key", sa.String(36), nullable=False, server_default=""),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("inferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trace_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_type",
            "source_id",
            "source_version_key",
            "target_type",
            "target_id",
            "target_version_key",
            "relation",
            name="uq_lineage_edges_object_lineage",
        ),
    )
    op.create_index(
        "ix_lineage_edges_project_source",
        "lineage_edges",
        ["project_id", "source_type", "source_id"],
    )
    op.create_index(
        "ix_lineage_edges_project_target",
        "lineage_edges",
        ["project_id", "target_type", "target_id"],
    )


def _edge_id(*parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, "lineage:" + ":".join(str(part or "") for part in parts)))


def _backfill_lineage() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    records: list[dict[str, object]] = []

    def add(
        project_id: str,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        relation: str,
        *,
        source_version_id: str | None = None,
        target_version_id: str | None = None,
        evidence: str,
    ) -> None:
        records.append(
            {
                "id": _edge_id(
                    project_id,
                    source_type,
                    source_id,
                    source_version_id,
                    target_type,
                    target_id,
                    target_version_id,
                    relation,
                ),
                "project_id": project_id,
                "source_type": source_type,
                "source_id": source_id,
                "source_version_id": source_version_id,
                "source_version_key": source_version_id or "",
                "target_type": target_type,
                "target_id": target_id,
                "target_version_id": target_version_id,
                "target_version_key": target_version_id or "",
                "relation": relation,
                "evidence": evidence,
                "inferred": False,
                "trace_id": None,
                "created_at": now,
            }
        )

    for row in bind.execute(
        sa.text(
            "SELECT c.project_id, c.id, c.source_story_bible_version_id, "
            "c.source_relationship_graph_id, c.current_profile_version_id, "
            "c.locked_identity_version_id, c.active_look_version_id, "
            "c.active_story_state_version_id FROM characters c"
        )
    ).mappings():
        for source_type, column in (
            ("StoryBibleVersion", "source_story_bible_version_id"),
            ("RelationshipGraphVersion", "source_relationship_graph_id"),
            ("CharacterVisualProfileVersion", "current_profile_version_id"),
            ("CharacterIdentityVersion", "locked_identity_version_id"),
            ("CharacterLookVersion", "active_look_version_id"),
            ("CharacterStoryStateVersion", "active_story_state_version_id"),
        ):
            if row[column]:
                add(
                    row["project_id"],
                    source_type,
                    row[column],
                    "Character",
                    row["id"],
                    "defines",
                    source_version_id=row[column],
                    evidence=f"characters.{column}",
                )

    for row in bind.execute(
        sa.text(
            "SELECT e.project_id, ss.id, ss.shot_id, ss.storyboard_version_id, "
            "ss.script_scene_id FROM shot_specs ss "
            "JOIN shots s ON s.id = ss.shot_id "
            "JOIN scenes sc ON sc.id = s.scene_id "
            "JOIN episodes e ON e.id = sc.episode_id"
        )
    ).mappings():
        add(
            row["project_id"],
            "ScriptScene",
            row["script_scene_id"],
            "ShotSpec",
            row["id"],
            "source_for",
            target_version_id=row["id"],
            evidence="shot_specs.script_scene_id",
        )
        add(
            row["project_id"],
            "ShotSpec",
            row["id"],
            "Shot",
            row["shot_id"],
            "specifies",
            source_version_id=row["id"],
            evidence="shot_specs.shot_id",
        )

    for row in bind.execute(
        sa.text(
            "SELECT e.project_id, t.id, t.shot_id, t.asset_id, t.generation_record_id "
            "FROM takes t JOIN shots s ON s.id = t.shot_id "
            "JOIN scenes sc ON sc.id = s.scene_id "
            "JOIN episodes e ON e.id = sc.episode_id"
        )
    ).mappings():
        add(
            row["project_id"],
            "Shot",
            row["shot_id"],
            "Take",
            row["id"],
            "has_take",
            target_version_id=row["id"],
            evidence="takes.shot_id",
        )
        add(
            row["project_id"],
            "Take",
            row["id"],
            "Asset",
            row["asset_id"],
            "renders_to",
            source_version_id=row["id"],
            evidence="takes.asset_id",
        )
        if row["generation_record_id"]:
            add(
                row["project_id"],
                "GenerationRecord",
                row["generation_record_id"],
                "Take",
                row["id"],
                "generated",
                target_version_id=row["id"],
                evidence="takes.generation_record_id",
            )

    if records:
        table = sa.table(
            "lineage_edges",
            *[sa.column(name) for name in records[0]],
        )
        for record in records:
            try:
                bind.execute(table.insert().values(**record))
            except sa.exc.IntegrityError:
                pass


def _backfill_shot_spec_revisions() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    revisions = sa.table(
        "shot_spec_revisions",
        sa.column("id"),
        sa.column("project_id"),
        sa.column("shot_id"),
        sa.column("shot_spec_id"),
        sa.column("version"),
        sa.column("parent_revision_id"),
        sa.column("source_storyboard_version_id"),
        sa.column("source_script_scene_id"),
        sa.column("source_script_line_ids_json"),
        sa.column("snapshot_json"),
        sa.column("change_json"),
        sa.column("change_reason"),
        sa.column("actor"),
        sa.column("content_hash"),
        sa.column("trace_id"),
        sa.column("created_at"),
    )
    for row in bind.execute(
        sa.text(
            "SELECT e.project_id, ss.* FROM shot_specs ss "
            "JOIN shots s ON s.id = ss.shot_id "
            "JOIN scenes sc ON sc.id = s.scene_id "
            "JOIN episodes e ON e.id = sc.episode_id"
        )
    ).mappings():
        snapshot = {
            key: row[key]
            for key in (
                "description",
                "dialogue",
                "duration_ms",
                "shot_size",
                "camera_movement",
                "character_look_ids_json",
                "location_version_id",
                "prop_version_ids_json",
                "prompt_json",
                "status",
            )
        }
        bind.execute(
            revisions.insert().values(
                id=_edge_id("shot-spec-revision", row["id"], 1),
                project_id=row["project_id"],
                shot_id=row["shot_id"],
                shot_spec_id=row["id"],
                version=1,
                parent_revision_id=None,
                source_storyboard_version_id=row["storyboard_version_id"],
                source_script_scene_id=row["script_scene_id"],
                source_script_line_ids_json=row["script_line_ids_json"],
                snapshot_json=json.dumps(
                    snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                change_json="{}",
                change_reason="历史 ShotSpec 初始快照",
                actor="migration:0028",
                content_hash=row["content_hash"],
                trace_id=None,
                created_at=now,
            )
        )


def upgrade() -> None:
    _add_generation_columns()
    _add_audit_columns()
    _add_review_columns()
    _create_shot_spec_revisions()
    _create_lineage_edges()
    _backfill_shot_spec_revisions()
    _backfill_lineage()


def downgrade() -> None:
    # Keep append-only provenance when downgrading on SQLite.
    pass
