"""Add dynamic storyboard versions and shot specifications.

Revision ID: 0014_storyboard_animatic
Revises: 0013_workflow_core
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_storyboard_animatic"
down_revision: str | None = "0013_workflow_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "storyboard_versions" not in tables:
        op.create_table(
            "storyboard_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "script_version_id",
                sa.String(36),
                sa.ForeignKey("script_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "visual_bible_version_id",
                sa.String(36),
                sa.ForeignKey("visual_bible_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "workflow_run_id",
                sa.String(36),
                sa.ForeignKey("workflow_runs.id"),
                nullable=True,
            ),
            sa.Column("episode_id", sa.String(36), sa.ForeignKey("episodes.id"), nullable=False),
            sa.Column("episode_ordinal", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("parent_version_id", sa.String(36), nullable=True),
            sa.Column("animatic_asset_id", sa.String(36), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "episode_ordinal", "version"),
        )
        op.create_index("ix_storyboard_versions_project_id", "storyboard_versions", ["project_id"])
        op.create_index(
            "ix_storyboard_versions_script_version_id",
            "storyboard_versions",
            ["script_version_id"],
        )
        op.create_index(
            "ix_storyboard_versions_visual_bible_version_id",
            "storyboard_versions",
            ["visual_bible_version_id"],
        )
        op.create_index(
            "ix_storyboard_versions_workflow_run_id",
            "storyboard_versions",
            ["workflow_run_id"],
        )
        op.create_index("ix_storyboard_versions_episode_id", "storyboard_versions", ["episode_id"])
        op.create_index("ix_storyboard_versions_status", "storyboard_versions", ["status"])
    if "shot_specs" not in tables:
        op.create_table(
            "shot_specs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "storyboard_version_id",
                sa.String(36),
                sa.ForeignKey("storyboard_versions.id"),
                nullable=False,
            ),
            sa.Column("shot_id", sa.String(36), sa.ForeignKey("shots.id"), nullable=False),
            sa.Column(
                "script_scene_id",
                sa.String(36),
                sa.ForeignKey("script_scenes.id"),
                nullable=False,
            ),
            sa.Column("script_line_ids_json", sa.Text(), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("dialogue", sa.Text(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("shot_size", sa.String(24), nullable=False),
            sa.Column("camera_movement", sa.String(24), nullable=False),
            sa.Column("character_look_ids_json", sa.Text(), nullable=False),
            sa.Column("location_version_id", sa.String(36), nullable=True),
            sa.Column("prop_version_ids_json", sa.Text(), nullable=False),
            sa.Column("prompt_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.UniqueConstraint("storyboard_version_id", "ordinal"),
            sa.UniqueConstraint("shot_id"),
        )
        op.create_index(
            "ix_shot_specs_storyboard_version_id", "shot_specs", ["storyboard_version_id"]
        )
        op.create_index("ix_shot_specs_shot_id", "shot_specs", ["shot_id"], unique=True)
        op.create_index("ix_shot_specs_script_scene_id", "shot_specs", ["script_scene_id"])
        op.create_index("ix_shot_specs_status", "shot_specs", ["status"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("shot_specs", "storyboard_versions"):
        if table in tables:
            op.drop_table(table)
