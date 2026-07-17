"""Link outlines and scripts to the approved relationship graph.

Revision ID: 0020_rel_script_links
Revises: 0019_character_rel_graph
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_rel_script_links"
down_revision: str | None = "0019_character_rel_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    for table in ("episode_outline_versions", "script_versions"):
        if "relationship_graph_version_id" in _column_names(table):
            continue
        if dialect == "sqlite":
            # SQLite batch mode recreates and drops the table. That fails on a
            # populated database when another existing table still references
            # it, even though the new column is nullable. SQLite supports the
            # non-destructive ADD COLUMN form directly; application validation
            # enforces that new records point at an approved graph.
            op.add_column(
                table,
                sa.Column("relationship_graph_version_id", sa.String(36), nullable=True),
            )
            op.create_index(
                f"ix_{table}_relationship_graph_version_id",
                table,
                ["relationship_graph_version_id"],
            )
            continue
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("relationship_graph_version_id", sa.String(36), nullable=True)
            )
            batch.create_foreign_key(
                f"fk_{table}_relationship_graph_version_id",
                "relationship_graph_versions",
                ["relationship_graph_version_id"],
                ["id"],
            )
            batch.create_index(
                f"ix_{table}_relationship_graph_version_id",
                ["relationship_graph_version_id"],
            )


def downgrade() -> None:
    for table in ("script_versions", "episode_outline_versions"):
        if "relationship_graph_version_id" not in _column_names(table):
            continue
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_relationship_graph_version_id")
            batch.drop_constraint(
                f"fk_{table}_relationship_graph_version_id",
                type_="foreignkey",
            )
            batch.drop_column("relationship_graph_version_id")
