"""Add versioned character looks and consent-aware voice profiles.

Revision ID: 0011_character_looks_voice
Revises: 0010_script_versions
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_character_looks_voice"
down_revision: str | None = "0010_script_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "character_look_versions" not in tables:
        op.create_table(
            "character_look_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "character_id", sa.String(36), sa.ForeignKey("characters.id"), nullable=False
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("label", sa.String(120), nullable=False),
            sa.Column("usage_scope", sa.String(80), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("reference_asset_ids_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("character_id", "version"),
        )
        op.create_index(
            "ix_character_look_versions_project_id", "character_look_versions", ["project_id"]
        )
        op.create_index(
            "ix_character_look_versions_character_id",
            "character_look_versions",
            ["character_id"],
        )
        op.create_index("ix_character_look_versions_status", "character_look_versions", ["status"])
    if "voice_profiles" not in tables:
        op.create_table(
            "voice_profiles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "character_id", sa.String(36), sa.ForeignKey("characters.id"), nullable=False
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(48), nullable=False),
            sa.Column("voice_key", sa.String(120), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("pronunciation_json", sa.Text(), nullable=False),
            sa.Column("consent_status", sa.String(32), nullable=False),
            sa.Column("cloning_enabled", sa.Boolean(), nullable=False),
            sa.Column("sample_asset_id", sa.String(36), nullable=True),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("character_id", "version"),
        )
        op.create_index("ix_voice_profiles_project_id", "voice_profiles", ["project_id"])
        op.create_index("ix_voice_profiles_character_id", "voice_profiles", ["character_id"])
        op.create_index("ix_voice_profiles_status", "voice_profiles", ["status"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("voice_profiles", "character_look_versions"):
        if table in tables:
            op.drop_table(table)
