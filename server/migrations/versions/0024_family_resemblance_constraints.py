# ruff: noqa: E501
"""Add explicit kinship metadata and versioned family resemblance constraints.

Revision ID: 0024_family_resemblance_constraints
Revises: 0023_character_visual_identity
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_family_resemblance_constraints"
down_revision: str | None = "0023_character_visual_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "family_kinship_json" not in _columns("relationship_edges"):
        op.add_column(
            "relationship_edges",
            sa.Column("family_kinship_json", sa.Text(), nullable=False, server_default="{}"),
        )

    if "character_family_resemblance_constraints" not in tables:
        op.create_table(
            "character_family_resemblance_constraints",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "character_id", sa.String(36), sa.ForeignKey("characters.id"), nullable=False
            ),
            sa.Column(
                "relationship_graph_version_id",
                sa.String(36),
                sa.ForeignKey("relationship_graph_versions.id"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("source_character_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column(
                "source_identity_version_ids_json", sa.Text(), nullable=False, server_default="[]"
            ),
            sa.Column("source_asset_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("relationship_evidence_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("inherited_features_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("similarity_level", sa.String(32), nullable=False),
            sa.Column("temperament_affinity_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column(
                "independence_constraints_json", sa.Text(), nullable=False, server_default="[]"
            ),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("character_id", "version"),
        )
        for column in (
            "project_id",
            "character_id",
            "relationship_graph_version_id",
            "status",
        ):
            op.create_index(
                f"ix_character_family_resemblance_constraints_{column}",
                "character_family_resemblance_constraints",
                [column],
            )

    if "family_constraint_version_id" not in _columns("character_candidate_batches"):
        # Keep this nullable and add it directly so populated SQLite databases can upgrade safely.
        op.add_column(
            "character_candidate_batches",
            sa.Column("family_constraint_version_id", sa.String(36), nullable=True),
        )
        op.create_index(
            "ix_character_candidate_batches_family_constraint_version_id",
            "character_candidate_batches",
            ["family_constraint_version_id"],
        )
        with op.batch_alter_table("character_candidate_batches") as batch:
            batch.create_foreign_key(
                "fk_character_candidate_batches_family_constraint_version_id",
                "character_family_resemblance_constraints",
                ["family_constraint_version_id"],
                ["id"],
            )


def downgrade() -> None:
    if "family_constraint_version_id" in _columns("character_candidate_batches"):
        with op.batch_alter_table("character_candidate_batches") as batch:
            batch.drop_constraint(
                "fk_character_candidate_batches_family_constraint_version_id",
                type_="foreignkey",
            )
        op.drop_index(
            "ix_character_candidate_batches_family_constraint_version_id",
            table_name="character_candidate_batches",
        )
        with op.batch_alter_table("character_candidate_batches") as batch:
            batch.drop_column("family_constraint_version_id")
    if "character_family_resemblance_constraints" in _tables():
        op.drop_table("character_family_resemblance_constraints")
    if "family_kinship_json" in _columns("relationship_edges"):
        with op.batch_alter_table("relationship_edges") as batch:
            batch.drop_column("family_kinship_json")
