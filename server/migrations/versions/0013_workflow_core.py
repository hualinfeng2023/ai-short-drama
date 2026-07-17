"""Add workflow runs, nodes, dependencies, and review gates.

Revision ID: 0013_workflow_core
Revises: 0012_visual_bible
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_workflow_core"
down_revision: str | None = "0012_visual_bible"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "workflow_runs" not in tables:
        op.create_table(
            "workflow_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("workflow_type", sa.String(64), nullable=False),
            sa.Column("source_entity_type", sa.String(48), nullable=False),
            sa.Column("source_entity_id", sa.String(36), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("current_gate", sa.String(40), nullable=True),
            sa.Column("config_version", sa.String(48), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_workflow_runs_project_id", "workflow_runs", ["project_id"])
        op.create_index("ix_workflow_runs_source_entity_id", "workflow_runs", ["source_entity_id"])
        op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    if "workflow_nodes" not in tables:
        op.create_table(
            "workflow_nodes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "workflow_run_id",
                sa.String(36),
                sa.ForeignKey("workflow_runs.id"),
                nullable=False,
            ),
            sa.Column("node_key", sa.String(80), nullable=False),
            sa.Column("node_type", sa.String(48), nullable=False),
            sa.Column("entity_type", sa.String(48), nullable=False),
            sa.Column("entity_id", sa.String(36), nullable=False),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("dependency_keys_json", sa.Text(), nullable=False),
            sa.Column("output_json", sa.Text(), nullable=False),
            sa.Column("degraded", sa.Boolean(), nullable=False),
            sa.Column("error_code", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("workflow_run_id", "node_key"),
        )
        op.create_index("ix_workflow_nodes_workflow_run_id", "workflow_nodes", ["workflow_run_id"])
        op.create_index("ix_workflow_nodes_entity_id", "workflow_nodes", ["entity_id"])
        op.create_index("ix_workflow_nodes_job_id", "workflow_nodes", ["job_id"])
        op.create_index("ix_workflow_nodes_status", "workflow_nodes", ["status"])
    if "job_dependencies" not in tables:
        op.create_table(
            "job_dependencies",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
            sa.Column("depends_on_job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
            sa.Column("dependency_type", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("job_id", "depends_on_job_id"),
        )
        op.create_index("ix_job_dependencies_job_id", "job_dependencies", ["job_id"])
        op.create_index(
            "ix_job_dependencies_depends_on_job_id",
            "job_dependencies",
            ["depends_on_job_id"],
        )
    if "review_gates" not in tables:
        op.create_table(
            "review_gates",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "workflow_run_id",
                sa.String(36),
                sa.ForeignKey("workflow_runs.id"),
                nullable=False,
            ),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("gate_key", sa.String(40), nullable=False),
            sa.Column("entity_type", sa.String(48), nullable=False),
            sa.Column("entity_id", sa.String(36), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("decision", sa.String(40), nullable=True),
            sa.Column("decided_by", sa.String(80), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("workflow_run_id", "gate_key"),
        )
        op.create_index("ix_review_gates_workflow_run_id", "review_gates", ["workflow_run_id"])
        op.create_index("ix_review_gates_project_id", "review_gates", ["project_id"])
        op.create_index("ix_review_gates_entity_id", "review_gates", ["entity_id"])
        op.create_index("ix_review_gates_status", "review_gates", ["status"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("review_gates", "job_dependencies", "workflow_nodes", "workflow_runs"):
        if table in tables:
            op.drop_table(table)
