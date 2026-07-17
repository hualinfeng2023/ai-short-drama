"""Add reference asset metadata and rights fields.

Revision ID: 0005_reference_asset_metadata
Revises: 0004_production_media_core
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_reference_asset_metadata"
down_revision: str | None = "0004_production_media_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("assets")}
    with op.batch_alter_table("assets") as batch:
        if "original_filename" not in columns:
            batch.add_column(sa.Column("original_filename", sa.String(255), nullable=True))
        if "metadata_json" not in columns:
            batch.add_column(
                sa.Column("metadata_json", sa.Text(), server_default="{}", nullable=False)
            )
        if "rights_status" not in columns:
            batch.add_column(
                sa.Column(
                    "rights_status",
                    sa.String(32),
                    server_default="RESTRICTED_DEMO",
                    nullable=False,
                )
            )


def downgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("assets")}
    with op.batch_alter_table("assets") as batch:
        if "rights_status" in columns:
            batch.drop_column("rights_status")
        if "metadata_json" in columns:
            batch.drop_column("metadata_json")
        if "original_filename" in columns:
            batch.drop_column("original_filename")
