"""Add proposal batches, Story DNA metadata, Story Bible, and episode outlines.

Revision ID: 0009_story_bible_outline
Revises: 0008_brief_targeting_v2
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_story_bible_outline"
down_revision: str | None = "0008_brief_targeting_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    proposal_columns = _columns("proposal_versions")
    with op.batch_alter_table("proposal_versions") as batch:
        for column in (
            sa.Column("batch_id", sa.String(36), nullable=True),
            sa.Column("direction_key", sa.String(40), server_default="legacy", nullable=False),
            sa.Column("source_proposal_ids_json", sa.Text(), server_default="[]", nullable=False),
            sa.Column("parent_version_id", sa.String(36), nullable=True),
            sa.Column(
                "schema_version",
                sa.String(32),
                server_default="story-direction-v1",
                nullable=False,
            ),
            sa.Column("generation_evidence_json", sa.Text(), server_default="{}", nullable=False),
        ):
            if column.name not in proposal_columns:
                batch.add_column(column)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("proposal_versions")}
    if "ix_proposal_versions_batch_id" not in indexes:
        op.create_index("ix_proposal_versions_batch_id", "proposal_versions", ["batch_id"])

    story_columns = _columns("story_versions")
    with op.batch_alter_table("story_versions") as batch:
        for column in (
            sa.Column("source_proposal_ids_json", sa.Text(), server_default="[]", nullable=False),
            sa.Column("parent_version_id", sa.String(36), nullable=True),
            sa.Column(
                "schema_version",
                sa.String(32),
                server_default="story-dna-v1",
                nullable=False,
            ),
            sa.Column("provider", sa.String(48), server_default="mock", nullable=False),
            sa.Column(
                "model",
                sa.String(80),
                server_default="deterministic-text-v2",
                nullable=False,
            ),
            sa.Column(
                "config_version",
                sa.String(48),
                server_default="story-package-v1",
                nullable=False,
            ),
        ):
            if column.name not in story_columns:
                batch.add_column(column)

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "proposal_batches" not in tables:
        op.create_table(
            "proposal_batches",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("brief_version", sa.Integer(), nullable=False),
            sa.Column("config_version", sa.String(48), nullable=False),
            sa.Column("provider", sa.String(48), nullable=False),
            sa.Column("model", sa.String(80), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("evidence_json", sa.Text(), server_default="{}", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "brief_version", "config_version"),
        )
        op.create_index("ix_proposal_batches_project_id", "proposal_batches", ["project_id"])
        op.create_index("ix_proposal_batches_status", "proposal_batches", ["status"])

    if "story_bible_versions" not in tables:
        op.create_table(
            "story_bible_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "story_version_id",
                sa.String(36),
                sa.ForeignKey("story_versions.id"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("critic_json", sa.Text(), server_default="{}", nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("parent_version_id", sa.String(36), nullable=True),
            sa.Column("schema_version", sa.String(32), nullable=False),
            sa.Column("provider", sa.String(48), nullable=False),
            sa.Column("model", sa.String(80), nullable=False),
            sa.Column("config_version", sa.String(48), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "version"),
        )
        op.create_index(
            "ix_story_bible_versions_project_id", "story_bible_versions", ["project_id"]
        )
        op.create_index(
            "ix_story_bible_versions_story_version_id",
            "story_bible_versions",
            ["story_version_id"],
        )
        op.create_index("ix_story_bible_versions_status", "story_bible_versions", ["status"])

    if "episode_outline_versions" not in tables:
        op.create_table(
            "episode_outline_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "story_bible_version_id",
                sa.String(36),
                sa.ForeignKey("story_bible_versions.id"),
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
            sa.Column("provider", sa.String(48), nullable=False),
            sa.Column("model", sa.String(80), nullable=False),
            sa.Column("config_version", sa.String(48), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "episode_ordinal", "version"),
        )
        op.create_index(
            "ix_episode_outline_versions_project_id",
            "episode_outline_versions",
            ["project_id"],
        )
        op.create_index(
            "ix_episode_outline_versions_story_bible_version_id",
            "episode_outline_versions",
            ["story_bible_version_id"],
        )
        op.create_index(
            "ix_episode_outline_versions_status", "episode_outline_versions", ["status"]
        )


def downgrade() -> None:
    for table in ("episode_outline_versions", "story_bible_versions", "proposal_batches"):
        if table in set(sa.inspect(op.get_bind()).get_table_names()):
            op.drop_table(table)
    story_columns = _columns("story_versions")
    with op.batch_alter_table("story_versions") as batch:
        for column in (
            "config_version",
            "model",
            "provider",
            "schema_version",
            "parent_version_id",
            "source_proposal_ids_json",
        ):
            if column in story_columns:
                batch.drop_column(column)
    proposal_columns = _columns("proposal_versions")
    with op.batch_alter_table("proposal_versions") as batch:
        for column in (
            "generation_evidence_json",
            "schema_version",
            "parent_version_id",
            "source_proposal_ids_json",
            "direction_key",
            "batch_id",
        ):
            if column in proposal_columns:
                batch.drop_column(column)
