"""Persist structured character consistency review decisions.

Revision ID: 0007_identity_review_workflow
Revises: 0006_character_consistency
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_identity_review_workflow"
down_revision: str | None = "0006_character_consistency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("takes")
    with op.batch_alter_table("takes") as batch:
        if "identity_review_decision" not in columns:
            batch.add_column(sa.Column("identity_review_decision", sa.String(40), nullable=True))
        if "identity_review_issues_json" not in columns:
            batch.add_column(
                sa.Column(
                    "identity_review_issues_json",
                    sa.Text(),
                    server_default="[]",
                    nullable=False,
                )
            )
        if "identity_review_note" not in columns:
            batch.add_column(sa.Column("identity_review_note", sa.Text(), nullable=True))
        if "identity_review_actor" not in columns:
            batch.add_column(sa.Column("identity_review_actor", sa.String(80), nullable=True))
        if "identity_reviewed_at" not in columns:
            batch.add_column(
                sa.Column("identity_reviewed_at", sa.DateTime(timezone=True), nullable=True)
            )
        if "identity_review_look_version" not in columns:
            batch.add_column(
                sa.Column("identity_review_look_version", sa.String(40), nullable=True)
            )


def downgrade() -> None:
    columns = _columns("takes")
    with op.batch_alter_table("takes") as batch:
        for column in (
            "identity_review_look_version",
            "identity_reviewed_at",
            "identity_review_actor",
            "identity_review_note",
            "identity_review_issues_json",
            "identity_review_decision",
        ):
            if column in columns:
                batch.drop_column(column)
