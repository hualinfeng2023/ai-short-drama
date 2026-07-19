"""Add versioned script excerpt rewrite candidates.

Revision ID: 0025_script_excerpt_revisions
Revises: 0024_family_resemblance_constraints
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_script_excerpt_revisions"
down_revision: str | None = "0024_family_resemblance_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "script_excerpt_revisions" in tables:
        return
    op.create_table(
        "script_excerpt_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "base_script_version_id",
            sa.String(36),
            sa.ForeignKey("script_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "base_line_id",
            sa.String(36),
            sa.ForeignKey("script_lines.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_revision_id",
            sa.String(36),
            sa.ForeignKey("script_excerpt_revisions.id"),
            nullable=True,
        ),
        sa.Column(
            "applied_script_version_id",
            sa.String(36),
            sa.ForeignKey("script_versions.id"),
            nullable=True,
        ),
        sa.Column("episode_ordinal", sa.Integer(), nullable=False),
        sa.Column("scene_ordinal", sa.Integer(), nullable=False),
        sa.Column("line_ordinal", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("selection_start", sa.Integer(), nullable=False),
        sa.Column("selection_end", sa.Integer(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("proposed_text", sa.Text(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("custom_instruction", sa.Text(), nullable=True),
        sa.Column("tone", sa.String(80), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(48), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id",
            "episode_ordinal",
            "scene_ordinal",
            "line_ordinal",
            "version",
        ),
    )
    for column in (
        "project_id",
        "base_script_version_id",
        "base_line_id",
        "parent_revision_id",
        "applied_script_version_id",
        "action",
        "status",
    ):
        op.create_index(
            f"ix_script_excerpt_revisions_{column}",
            "script_excerpt_revisions",
            [column],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "script_excerpt_revisions" in tables:
        op.drop_table("script_excerpt_revisions")
