"""Add structured audience, market, language, and platform targets to Brief versions.

Revision ID: 0008_brief_targeting_v2
Revises: 0007_identity_review_workflow
Create Date: 2026-07-14
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_brief_targeting_v2"
down_revision: str | None = "0007_identity_review_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("brief_versions")
    additions = (
        sa.Column("primary_audience", sa.String(80), server_default="general", nullable=False),
        sa.Column("secondary_audiences_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("primary_market", sa.String(16), server_default="CN", nullable=False),
        sa.Column("secondary_markets_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("canonical_language", sa.String(24), server_default="zh-CN", nullable=False),
        sa.Column("localization_targets_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("platform_targets_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("content_requirements_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("content_avoidances_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("creative_defaults_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("blocking_questions_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column(
            "payload_schema_version",
            sa.String(32),
            server_default="brief-v2",
            nullable=False,
        ),
    )
    with op.batch_alter_table("brief_versions") as batch:
        for column in additions:
            if column.name not in columns:
                batch.add_column(column)

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, target_platform, aspect_ratio, target_duration_sec "
            "FROM brief_versions WHERE platform_targets_json = '[]'"
        )
    ).mappings()
    for row in rows:
        targets = [
            {
                "platform": str(row["target_platform"]),
                "priority": "PRIMARY",
                "aspect_ratio": str(row["aspect_ratio"]),
                "target_duration_sec": int(row["target_duration_sec"]),
                "caption_mode": "BOTH",
            }
        ]
        bind.execute(
            sa.text(
                "UPDATE brief_versions SET platform_targets_json = :targets WHERE id = :brief_id"
            ),
            {"brief_id": row["id"], "targets": json.dumps(targets, separators=(",", ":"))},
        )


def downgrade() -> None:
    columns = _columns("brief_versions")
    with op.batch_alter_table("brief_versions") as batch:
        for column in (
            "payload_schema_version",
            "blocking_questions_json",
            "creative_defaults_json",
            "content_avoidances_json",
            "content_requirements_json",
            "platform_targets_json",
            "localization_targets_json",
            "canonical_language",
            "secondary_markets_json",
            "primary_market",
            "secondary_audiences_json",
            "primary_audience",
        ):
            if column in columns:
                batch.drop_column(column)
