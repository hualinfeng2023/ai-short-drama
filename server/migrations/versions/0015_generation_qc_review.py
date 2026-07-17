"""Add generation lineage, generic quality checks, and review records.

Revision ID: 0015_generation_qc_review
Revises: 0014_storyboard_animatic
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_generation_qc_review"
down_revision: str | None = "0014_storyboard_animatic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    take_columns = _columns("takes")
    with op.batch_alter_table("takes") as batch:
        if "generation_record_id" not in take_columns:
            batch.add_column(sa.Column("generation_record_id", sa.String(36), nullable=True))
        if "quality_status" not in take_columns:
            batch.add_column(
                sa.Column(
                    "quality_status",
                    sa.String(32),
                    server_default="NOT_CHECKED",
                    nullable=False,
                )
            )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("takes")}
    if "ix_takes_generation_record_id" not in indexes:
        op.create_index("ix_takes_generation_record_id", "takes", ["generation_record_id"])
    if "ix_takes_one_current_per_shot" in indexes:
        op.drop_index("ix_takes_one_current_per_shot", table_name="takes")
    if "ix_takes_one_current_per_shot_kind" not in indexes:
        op.create_index(
            "ix_takes_one_current_per_shot_kind",
            "takes",
            ["shot_id", "kind"],
            unique=True,
            sqlite_where=sa.text("is_current = 1"),
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "generation_records" not in tables:
        op.create_table(
            "generation_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True),
            sa.Column("entity_type", sa.String(48), nullable=False),
            sa.Column("entity_id", sa.String(36), nullable=False),
            sa.Column("capability", sa.String(48), nullable=False),
            sa.Column("provider", sa.String(48), nullable=False),
            sa.Column("model", sa.String(80), nullable=False),
            sa.Column("config_version", sa.String(48), nullable=False),
            sa.Column("prompt_hash", sa.String(64), nullable=False),
            sa.Column("seed", sa.String(80), nullable=True),
            sa.Column("reference_asset_ids_json", sa.Text(), nullable=False),
            sa.Column("provider_request_id", sa.String(160), nullable=True),
            sa.Column("provider_task_id", sa.String(160), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("input_units", sa.Float(), nullable=True),
            sa.Column("output_units", sa.Float(), nullable=True),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.Column("output_asset_id", sa.String(36), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        for column in ("project_id", "job_id", "entity_id", "capability", "status"):
            op.create_index(f"ix_generation_records_{column}", "generation_records", [column])
    if "quality_checks" not in tables:
        op.create_table(
            "quality_checks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "generation_record_id",
                sa.String(36),
                sa.ForeignKey("generation_records.id"),
                nullable=False,
            ),
            sa.Column("check_type", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("findings_json", sa.Text(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("generation_record_id", "check_type"),
        )
        op.create_index("ix_quality_checks_project_id", "quality_checks", ["project_id"])
        op.create_index(
            "ix_quality_checks_generation_record_id",
            "quality_checks",
            ["generation_record_id"],
        )
        op.create_index("ix_quality_checks_status", "quality_checks", ["status"])
    if "review_records" not in tables:
        op.create_table(
            "review_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("entity_type", sa.String(48), nullable=False),
            sa.Column("entity_id", sa.String(36), nullable=False),
            sa.Column("gate_key", sa.String(40), nullable=False),
            sa.Column("risk_level", sa.String(24), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("decision", sa.String(40), nullable=True),
            sa.Column("issues_json", sa.Text(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("actor", sa.String(80), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in ("project_id", "entity_type", "entity_id", "gate_key", "status"):
            op.create_index(f"ix_review_records_{column}", "review_records", [column])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("review_records", "quality_checks", "generation_records"):
        if table in tables:
            op.drop_table(table)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("takes")}
    if "ix_takes_one_current_per_shot_kind" in indexes:
        op.drop_index("ix_takes_one_current_per_shot_kind", table_name="takes")
    if "ix_takes_one_current_per_shot" not in indexes:
        op.create_index(
            "ix_takes_one_current_per_shot",
            "takes",
            ["shot_id"],
            unique=True,
            sqlite_where=sa.text("is_current = 1"),
        )
    take_columns = _columns("takes")
    with op.batch_alter_table("takes") as batch:
        for column in ("quality_status", "generation_record_id"):
            if column in take_columns:
                batch.drop_column(column)
