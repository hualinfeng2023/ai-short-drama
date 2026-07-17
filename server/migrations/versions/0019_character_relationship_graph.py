"""Add versioned character relationship graph entities.

Revision ID: 0019_character_rel_graph
Revises: 0018_export_profiles_v2
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_character_rel_graph"
down_revision: str | None = "0018_export_profiles_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "relationship_graph_versions" not in tables:
        op.create_table(
            "relationship_graph_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "story_bible_version_id",
                sa.String(36),
                sa.ForeignKey("story_bible_versions.id"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("parent_version_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column(
                "schema_version",
                sa.String(32),
                nullable=False,
                server_default="relationship-graph-v1",
            ),
            sa.Column(
                "config_version",
                sa.String(48),
                nullable=False,
                server_default="relationship-graph-v1",
            ),
            sa.Column("provider", sa.String(48), nullable=False, server_default="mock"),
            sa.Column(
                "model",
                sa.String(80),
                nullable=False,
                server_default="deterministic-text-v2",
            ),
            sa.Column("critic_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "version"),
        )
        for column in ("project_id", "story_bible_version_id", "status"):
            op.create_index(
                f"ix_relationship_graph_versions_{column}",
                "relationship_graph_versions",
                [column],
            )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "relationship_edges" not in tables:
        op.create_table(
            "relationship_edges",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "graph_version_id",
                sa.String(36),
                sa.ForeignKey("relationship_graph_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("relationship_key", sa.String(80), nullable=False),
            sa.Column("character_pair_key", sa.String(161), nullable=False),
            sa.Column("source_character_key", sa.String(80), nullable=False),
            sa.Column("target_character_key", sa.String(80), nullable=False),
            sa.Column("directionality", sa.String(24), nullable=False),
            sa.Column("relationship_types_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("surface_relationship", sa.Text(), nullable=False),
            sa.Column("true_relationship", sa.Text(), nullable=False),
            sa.Column("source_view_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("target_view_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("trust_level", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "emotional_temperature",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("power_balance", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("conflict_intensity", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("story_function", sa.Text(), nullable=False),
            sa.Column("secret", sa.Text(), nullable=True),
            sa.Column("is_core", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.UniqueConstraint("graph_version_id", "relationship_key"),
            sa.UniqueConstraint("graph_version_id", "character_pair_key"),
        )
        for column in (
            "graph_version_id",
            "source_character_key",
            "target_character_key",
        ):
            op.create_index(f"ix_relationship_edges_{column}", "relationship_edges", [column])

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "relationship_beats" not in tables:
        op.create_table(
            "relationship_beats",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "graph_version_id",
                sa.String(36),
                sa.ForeignKey("relationship_graph_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "relationship_edge_id",
                sa.String(36),
                sa.ForeignKey("relationship_edges.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("episode_ordinal", sa.Integer(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("scene_ordinal", sa.Integer(), nullable=True),
            sa.Column("trigger_type", sa.String(32), nullable=False),
            sa.Column("trigger_ref", sa.String(120), nullable=True),
            sa.Column("before_state_json", sa.Text(), nullable=False),
            sa.Column("after_state_json", sa.Text(), nullable=False),
            sa.Column("evidence", sa.Text(), nullable=False),
            sa.Column("emotional_consequence", sa.Text(), nullable=False),
            sa.Column("audience_visibility", sa.String(24), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.UniqueConstraint("relationship_edge_id", "episode_ordinal", "sequence"),
        )
        for column in (
            "graph_version_id",
            "relationship_edge_id",
            "episode_ordinal",
            "trigger_type",
        ):
            op.create_index(f"ix_relationship_beats_{column}", "relationship_beats", [column])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in (
        "relationship_beats",
        "relationship_edges",
        "relationship_graph_versions",
    ):
        if table in tables:
            op.drop_table(table)
