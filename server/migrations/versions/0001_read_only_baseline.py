"""Create the read-only workspace baseline.

Revision ID: 0001_read_only_baseline
Revises:
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_read_only_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("idea", sa.Text(), nullable=False),
        sa.Column("genre", sa.String(length=80), nullable=False),
        sa.Column("style", sa.String(length=80), nullable=False),
        sa.Column("target_duration_sec", sa.Integer(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("available_points", sa.Integer(), nullable=False),
        sa.Column("timeline_version", sa.Integer(), nullable=False),
        sa.Column("preview_approved", sa.Boolean(), nullable=False),
        sa.Column("export_ready", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_updated_at", "projects", ["updated_at"])

    op.create_table(
        "episodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("target_duration_sec", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_episodes_project_id", "episodes", ["project_id"])
    op.create_index("ix_episodes_status", "episodes", ["status"])

    op.create_table(
        "scenes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("episode_id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("duration_sec", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenes_episode_id", "scenes", ["episode_id"])
    op.create_index("ix_scenes_status", "scenes", ["status"])

    op.create_table(
        "shots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scene_id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("dialogue", sa.Text(), nullable=False),
        sa.Column("duration_sec", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("shot_size", sa.String(length=24), nullable=False),
        sa.Column("camera_movement", sa.String(length=24), nullable=False),
        sa.Column("current_take", sa.Integer(), nullable=False),
        sa.Column("candidate_take", sa.Integer(), nullable=True),
        sa.Column("continuity", sa.String(length=24), nullable=False),
        sa.Column("location", sa.String(length=120), nullable=False),
        sa.Column("time_of_day", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shots_scene_id", "shots", ["scene_id"])
    op.create_index("ix_shots_status", "shots", ["status"])

    op.create_table(
        "jobs",
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
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_project_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_shots_status", table_name="shots")
    op.drop_index("ix_shots_scene_id", table_name="shots")
    op.drop_table("shots")
    op.drop_index("ix_scenes_status", table_name="scenes")
    op.drop_index("ix_scenes_episode_id", table_name="scenes")
    op.drop_table("scenes")
    op.drop_index("ix_episodes_status", table_name="episodes")
    op.drop_index("ix_episodes_project_id", table_name="episodes")
    op.drop_table("episodes")
    op.drop_index("ix_projects_updated_at", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_table("projects")
