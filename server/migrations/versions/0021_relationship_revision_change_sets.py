"""Allow change sets to track relationship graph revisions.

Revision ID: 0021_rel_revision_sets
Revises: 0020_rel_script_links
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_rel_revision_sets"
down_revision: str | None = "0020_rel_script_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _column_names("change_sets")
    with op.batch_alter_table("change_sets") as batch:
        batch.alter_column("base_timeline_id", existing_type=sa.String(36), nullable=True)
        if "base_relationship_graph_id" not in columns:
            batch.add_column(sa.Column("base_relationship_graph_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_change_sets_base_relationship_graph_id",
                "relationship_graph_versions",
                ["base_relationship_graph_id"],
                ["id"],
            )
            batch.create_index(
                "ix_change_sets_base_relationship_graph_id", ["base_relationship_graph_id"]
            )
        if "result_relationship_graph_id" not in columns:
            batch.add_column(
                sa.Column("result_relationship_graph_id", sa.String(36), nullable=True)
            )
            batch.create_foreign_key(
                "fk_change_sets_result_relationship_graph_id",
                "relationship_graph_versions",
                ["result_relationship_graph_id"],
                ["id"],
            )


def downgrade() -> None:
    columns = _column_names("change_sets")
    with op.batch_alter_table("change_sets") as batch:
        if "result_relationship_graph_id" in columns:
            batch.drop_constraint("fk_change_sets_result_relationship_graph_id", type_="foreignkey")
            batch.drop_column("result_relationship_graph_id")
        if "base_relationship_graph_id" in columns:
            batch.drop_index("ix_change_sets_base_relationship_graph_id")
            batch.drop_constraint("fk_change_sets_base_relationship_graph_id", type_="foreignkey")
            batch.drop_column("base_relationship_graph_id")
        batch.alter_column("base_timeline_id", existing_type=sa.String(36), nullable=False)
