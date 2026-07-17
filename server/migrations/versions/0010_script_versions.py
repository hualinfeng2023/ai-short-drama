"""Add structured episode scripts, scenes, and lines.

Revision ID: 0010_script_versions
Revises: 0009_story_bible_outline
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_script_versions"
down_revision: str | None = "0009_story_bible_outline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "script_versions" not in tables:
        op.create_table(
            "script_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "outline_version_id",
                sa.String(36),
                sa.ForeignKey("episode_outline_versions.id"),
                nullable=False,
            ),
            sa.Column("episode_ordinal", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("critic_json", sa.Text(), server_default="{}", nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("parent_version_id", sa.String(36), nullable=True),
            sa.Column("schema_version", sa.String(32), nullable=False),
            sa.Column("canonical_language", sa.String(24), nullable=False),
            sa.Column("provider", sa.String(48), nullable=False),
            sa.Column("model", sa.String(80), nullable=False),
            sa.Column("config_version", sa.String(48), nullable=False),
            sa.Column("estimated_duration_ms", sa.Integer(), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "episode_ordinal", "version"),
        )
        op.create_index("ix_script_versions_project_id", "script_versions", ["project_id"])
        op.create_index(
            "ix_script_versions_outline_version_id", "script_versions", ["outline_version_id"]
        )
        op.create_index("ix_script_versions_status", "script_versions", ["status"])

    if "script_scenes" not in tables:
        op.create_table(
            "script_scenes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "script_version_id",
                sa.String(36),
                sa.ForeignKey("script_versions.id"),
                nullable=False,
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("heading", sa.String(180), nullable=False),
            sa.Column("location", sa.String(120), nullable=False),
            sa.Column("time_of_day", sa.String(40), nullable=False),
            sa.Column("purpose", sa.Text(), nullable=False),
            sa.Column("emotion", sa.String(80), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("bgm_intent", sa.Text(), server_default="", nullable=False),
            sa.Column("sfx_intent_json", sa.Text(), server_default="[]", nullable=False),
            sa.UniqueConstraint("script_version_id", "ordinal"),
        )
        op.create_index(
            "ix_script_scenes_script_version_id", "script_scenes", ["script_version_id"]
        )

    if "script_lines" not in tables:
        op.create_table(
            "script_lines",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "script_scene_id",
                sa.String(36),
                sa.ForeignKey("script_scenes.id"),
                nullable=False,
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("speaker_key", sa.String(80), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("line_type", sa.String(32), nullable=False),
            sa.Column("emotion", sa.String(80), nullable=False),
            sa.Column("speech_rate", sa.Float(), nullable=False),
            sa.Column("pause_after_ms", sa.Integer(), nullable=False),
            sa.Column("estimated_duration_ms", sa.Integer(), nullable=False),
            sa.Column("pronunciation_json", sa.Text(), server_default="{}", nullable=False),
            sa.Column("localization_json", sa.Text(), server_default="{}", nullable=False),
            sa.UniqueConstraint("script_scene_id", "ordinal"),
        )
        op.create_index("ix_script_lines_script_scene_id", "script_lines", ["script_scene_id"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("script_lines", "script_scenes", "script_versions"):
        if table in tables:
            op.drop_table(table)
