"""Add project writes, brief versions, and idempotency records.

Revision ID: 0002_project_brief_writes
Revises: 0001_read_only_baseline
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_project_brief_writes"
down_revision: str | None = "0001_read_only_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    project_columns = {column["name"] for column in inspector.get_columns("projects")}
    if "target_platform" not in project_columns:
        op.add_column(
            "projects",
            sa.Column(
                "target_platform", sa.String(length=40), nullable=False, server_default="douyin"
            ),
        )
    if "lock_version" not in project_columns:
        op.add_column(
            "projects",
            sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        )
    if "created_at" not in project_columns:
        op.add_column(
            "projects",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("'1970-01-01 00:00:00'"),
            ),
        )
    op.execute(
        sa.text(
            "UPDATE projects SET created_at = updated_at "
            "WHERE created_at IS NULL OR created_at = '1970-01-01 00:00:00'"
        )
    )

    tables = set(sa.inspect(bind).get_table_names())
    if "brief_versions" not in tables:
        op.create_table(
            "brief_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("project_name", sa.String(length=120), nullable=False),
            sa.Column("raw_input", sa.Text(), nullable=False),
            sa.Column("genre", sa.String(length=80), nullable=False),
            sa.Column("style", sa.String(length=80), nullable=False),
            sa.Column("target_duration_sec", sa.Integer(), nullable=False),
            sa.Column("aspect_ratio", sa.String(length=8), nullable=False),
            sa.Column("target_platform", sa.String(length=40), nullable=False),
            sa.Column("reference_asset_ids_json", sa.Text(), nullable=False),
            sa.Column("assumptions_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "version"),
        )
    brief_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("brief_versions")}
    if "ix_brief_versions_project_id" not in brief_indexes:
        op.create_index("ix_brief_versions_project_id", "brief_versions", ["project_id"])

    tables = set(sa.inspect(bind).get_table_names())
    if "idempotency_keys" not in tables:
        op.create_table(
            "idempotency_keys",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("scope", sa.String(length=120), nullable=False),
            sa.Column("key", sa.String(length=160), nullable=False),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("response_json", sa.Text(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("resource_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("scope", "key"),
        )
    idempotency_indexes = {
        item["name"] for item in sa.inspect(bind).get_indexes("idempotency_keys")
    }
    if "ix_idempotency_keys_scope" not in idempotency_indexes:
        op.create_index("ix_idempotency_keys_scope", "idempotency_keys", ["scope"])
    if "ix_idempotency_keys_resource_id" not in idempotency_indexes:
        op.create_index("ix_idempotency_keys_resource_id", "idempotency_keys", ["resource_id"])
    if "ix_idempotency_keys_expires_at" not in idempotency_indexes:
        op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_resource_id", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_scope", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_brief_versions_project_id", table_name="brief_versions")
    op.drop_table("brief_versions")
    op.drop_column("projects", "created_at")
    op.drop_column("projects", "lock_version")
    op.drop_column("projects", "target_platform")
