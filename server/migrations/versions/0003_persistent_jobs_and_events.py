"""Add persistent jobs, worker state, events, and proposals.

Revision ID: 0003_persistent_jobs_events
Revises: 0002_project_brief_writes
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_persistent_jobs_events"
down_revision: str | None = "0002_project_brief_writes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def create_jobs_table(name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=48), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=220), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("entity", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("stage", sa.String(length=160), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=80), nullable=True),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("estimated_seconds", sa.Integer(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def ensure_job_indexes(bind) -> None:  # noqa: ANN001
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("jobs")}
    definitions = {
        "ix_jobs_project_id": ["project_id"],
        "ix_jobs_status": ["status"],
        "ix_jobs_job_type": ["job_type"],
        "ix_jobs_entity_id": ["entity_id"],
        "ix_jobs_available_at": ["available_at"],
        "ix_jobs_claim": ["status", "available_at", "priority"],
    }
    for name, columns in definitions.items():
        if name not in indexes:
            op.create_index(name, "jobs", columns)
    if "ix_jobs_idempotency_key" not in indexes:
        op.create_index("ix_jobs_idempotency_key", "jobs", ["idempotency_key"], unique=True)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "jobs" not in tables and "jobs_v3" in tables:
        op.rename_table("jobs_v3", "jobs")
    elif "jobs" in tables:
        job_columns = {column["name"] for column in sa.inspect(bind).get_columns("jobs")}
        if "job_type" not in job_columns:
            if "jobs_v3" in tables:
                op.drop_table("jobs_v3")
            create_jobs_table("jobs_v3")
            op.execute(
                sa.text(
                    """
                    INSERT INTO jobs_v3 (
                        id, project_id, job_type, entity_type, entity_id,
                        idempotency_key, request_hash, label, entity, status,
                        progress, stage, priority, attempt, max_attempts,
                        available_at, lease_until, heartbeat_at, cancel_requested,
                        input_json, output_json, error_code, error_message,
                        error_details_json, created_at, created_at_utc, updated_at,
                        completed_at, worker_id, trace_id, estimated_seconds, retryable
                    )
                    SELECT
                        id,
                        project_id,
                        CASE WHEN label LIKE 'S05%' THEN 'DEMO_RENDER' ELSE 'LEGACY_DEMO' END,
                        'project',
                        project_id,
                        'legacy:' || id,
                        'legacy',
                        label,
                        entity,
                        status,
                        progress,
                        stage,
                        0,
                        CASE WHEN status IN ('RUNNING', 'FAILED') THEN 1 ELSE 0 END,
                        3,
                        CURRENT_TIMESTAMP,
                        NULL,
                        NULL,
                        0,
                        CASE
                            WHEN label LIKE 'S05%'
                            THEN '{"steps"' || char(58) || '4}'
                            ELSE '{}'
                        END,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        created_at,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP,
                        CASE WHEN status = 'SUCCEEDED' THEN CURRENT_TIMESTAMP ELSE NULL END,
                        NULL,
                        id,
                        estimated_seconds,
                        retryable
                    FROM jobs
                    """
                )
            )
            op.drop_table("jobs")
            op.rename_table("jobs_v3", "jobs")
    ensure_job_indexes(bind)

    tables = set(sa.inspect(bind).get_table_names())
    if "proposal_versions" not in tables:
        op.create_table(
            "proposal_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("brief_version", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("provider", sa.String(length=48), nullable=False),
            sa.Column("model", sa.String(length=80), nullable=False),
            sa.Column("config_version", sa.String(length=48), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "version"),
        )
        op.create_index("ix_proposal_versions_project_id", "proposal_versions", ["project_id"])
        op.create_index("ix_proposal_versions_status", "proposal_versions", ["status"])

    tables = set(sa.inspect(bind).get_table_names())
    if "event_log" not in tables:
        op.create_table(
            "event_log",
            sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("job_id", sa.String(length=36), nullable=True),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("sequence"),
            sa.UniqueConstraint("event_id"),
        )
        op.create_index("ix_event_log_project_id", "event_log", ["project_id"])
        op.create_index("ix_event_log_job_id", "event_log", ["job_id"])
        op.create_index("ix_event_log_event_type", "event_log", ["event_type"])
        op.create_index("ix_event_log_created_at", "event_log", ["created_at"])
        op.create_index("ix_event_log_project_sequence", "event_log", ["project_id", "sequence"])

    tables = set(sa.inspect(bind).get_table_names())
    if "worker_state" not in tables:
        op.create_table(
            "worker_state",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("worker_id", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("current_job_id", sa.String(length=36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("worker_id"),
        )
        op.create_index("ix_worker_state_heartbeat_at", "worker_state", ["heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_worker_state_heartbeat_at", table_name="worker_state")
    op.drop_table("worker_state")
    op.drop_index("ix_event_log_project_sequence", table_name="event_log")
    op.drop_index("ix_event_log_created_at", table_name="event_log")
    op.drop_index("ix_event_log_event_type", table_name="event_log")
    op.drop_index("ix_event_log_job_id", table_name="event_log")
    op.drop_index("ix_event_log_project_id", table_name="event_log")
    op.drop_table("event_log")
    op.drop_index("ix_proposal_versions_status", table_name="proposal_versions")
    op.drop_index("ix_proposal_versions_project_id", table_name="proposal_versions")
    op.drop_table("proposal_versions")

    op.create_table(
        "jobs_v2",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("entity", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("stage", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("estimated_seconds", sa.Integer(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO jobs_v2 (
                id, project_id, label, entity, status, progress, stage,
                created_at, estimated_seconds, retryable
            )
            SELECT id, project_id, label, entity, status, progress, stage,
                   created_at, estimated_seconds, retryable
            FROM jobs
            """
        )
    )
    op.drop_table("jobs")
    op.rename_table("jobs_v2", "jobs")
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
