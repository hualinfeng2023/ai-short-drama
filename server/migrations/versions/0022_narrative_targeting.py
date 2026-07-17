"""Split protagonist, audience and emotional reward targeting.

Revision ID: 0022_narrative_targeting
Revises: 0021_rel_revision_sets
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_narrative_targeting"
down_revision: str | None = "0021_rel_revision_sets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _column_names("brief_versions")
    with op.batch_alter_table("brief_versions") as batch:
        if "narrative_protagonist" not in columns:
            batch.add_column(
                sa.Column(
                    "narrative_protagonist",
                    sa.String(24),
                    nullable=False,
                    server_default="unspecified",
                )
            )
        if "target_audience" not in columns:
            batch.add_column(
                sa.Column(
                    "target_audience",
                    sa.String(24),
                    nullable=False,
                    server_default="general",
                )
            )
        if "emotional_rewards_json" not in columns:
            batch.add_column(
                sa.Column(
                    "emotional_rewards_json",
                    sa.Text(),
                    nullable=False,
                    server_default="[]",
                )
            )
        if "audience_profile" not in columns:
            batch.add_column(
                sa.Column(
                    "audience_profile",
                    sa.String(240),
                    nullable=False,
                    server_default="",
                )
            )
        if "production_format" not in columns:
            batch.add_column(
                sa.Column(
                    "production_format",
                    sa.String(32),
                    nullable=False,
                    server_default="live_action",
                )
            )

    # Preserve the old mixed audience label only as a demographic/profile note.
    # Do not infer a content orientation or protagonist from historical data.
    op.execute(
        sa.text(
            """
            UPDATE brief_versions
            SET audience_profile = CASE primary_audience
                WHEN 'urban_women_25_34' THEN '都市女性 25–34（历史项目画像）'
                WHEN 'young_adults' THEN '年轻成人（历史项目画像）'
                WHEN 'suspense_fans' THEN '悬疑爱好者（历史项目画像）'
                WHEN 'mobile_first_viewers' THEN '移动端重度用户（历史项目画像）'
                ELSE audience_profile
            END
            WHERE audience_profile = '' AND primary_audience != 'general'
            """
        )
    )


def downgrade() -> None:
    columns = _column_names("brief_versions")
    with op.batch_alter_table("brief_versions") as batch:
        for column in (
            "production_format",
            "audience_profile",
            "emotional_rewards_json",
            "target_audience",
            "narrative_protagonist",
        ):
            if column in columns:
                batch.drop_column(column)
