"""Persist a project-level generated thumbnail reference.

Revision ID: 0027_project_thumbnails
Revises: 0026_dependency_edges
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_project_thumbnails"
down_revision: str | None = "0026_dependency_edges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")}
    if "thumbnail_asset_id" not in columns:
        op.add_column("projects", sa.Column("thumbnail_asset_id", sa.String(36), nullable=True))


def downgrade() -> None:
    # SQLite does not support a non-destructive DROP COLUMN for populated tables.
    pass
