"""Add location, prop, and visual bible versions.

Revision ID: 0012_visual_bible
Revises: 0011_character_looks_voice
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_visual_bible"
down_revision: str | None = "0011_character_looks_voice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _version_table(
    table: str,
    key_column: str,
) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(key_column, sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("reference_asset_ids_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", key_column, "version"),
    )
    op.create_index(f"ix_{table}_project_id", table, ["project_id"])
    op.create_index(f"ix_{table}_status", table, ["status"])


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "location_versions" not in tables:
        _version_table("location_versions", "location_key")
    if "prop_versions" not in tables:
        _version_table("prop_versions", "prop_key")
    if "visual_bible_versions" not in tables:
        op.create_table(
            "visual_bible_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("character_look_ids_json", sa.Text(), nullable=False),
            sa.Column("location_version_ids_json", sa.Text(), nullable=False),
            sa.Column("prop_version_ids_json", sa.Text(), nullable=False),
            sa.Column("voice_profile_ids_json", sa.Text(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "version"),
        )
        op.create_index(
            "ix_visual_bible_versions_project_id", "visual_bible_versions", ["project_id"]
        )
        op.create_index("ix_visual_bible_versions_status", "visual_bible_versions", ["status"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("visual_bible_versions", "prop_versions", "location_versions"):
        if table in tables:
            op.drop_table(table)
