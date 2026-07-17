"""Add export profiles, rights preflight, and delivery artifacts.

Revision ID: 0018_export_profiles_v2
Revises: 0017_multitrack_timeline
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_export_profiles_v2"
down_revision: str | None = "0017_multitrack_timeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    export_columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("exports")}
    with op.batch_alter_table("exports") as batch:
        additions = (
            ("export_profile_id", sa.String(36), None),
            ("language", sa.String(24), "zh-CN"),
            ("rights_preflight_id", sa.String(36), None),
            ("picture_master_asset_id", sa.String(36), None),
            ("cover_asset_id", sa.String(36), None),
            ("stems_manifest_asset_id", sa.String(36), None),
            ("qc_report_asset_id", sa.String(36), None),
        )
        for name, column_type, default in additions:
            if name not in export_columns:
                batch.add_column(
                    sa.Column(
                        name,
                        column_type,
                        nullable=default is None,
                        server_default=default,
                    )
                )
    export_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("exports")}
    for column in ("export_profile_id", "language"):
        name = f"ix_exports_{column}"
        if name not in export_indexes:
            op.create_index(name, "exports", [column])

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "export_profiles" not in tables:
        op.create_table(
            "export_profiles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(40), nullable=False),
            sa.Column("aspect_ratio", sa.String(8), nullable=False),
            sa.Column("width", sa.Integer(), nullable=False),
            sa.Column("height", sa.Integer(), nullable=False),
            sa.Column("caption_mode", sa.String(24), nullable=False),
            sa.Column("languages_json", sa.Text(), nullable=False),
            sa.Column("audio_tracks_json", sa.Text(), nullable=False),
            sa.Column("watermark_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "name", "version"),
        )
        op.create_index("ix_export_profiles_project_id", "export_profiles", ["project_id"])
        op.create_index("ix_export_profiles_platform", "export_profiles", ["platform"])
        op.create_index("ix_export_profiles_status", "export_profiles", ["status"])
    if "rights_preflights" not in tables:
        op.create_table(
            "rights_preflights",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "timeline_id",
                sa.String(36),
                sa.ForeignKey("timeline_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "export_profile_id",
                sa.String(36),
                sa.ForeignKey("export_profiles.id"),
                nullable=False,
            ),
            sa.Column("language", sa.String(24), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("blockers_json", sa.Text(), nullable=False),
            sa.Column("checks_json", sa.Text(), nullable=False),
            sa.Column("policy_version", sa.String(48), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in ("project_id", "timeline_id", "export_profile_id", "status"):
            op.create_index(f"ix_rights_preflights_{column}", "rights_preflights", [column])
    if "export_artifacts" not in tables:
        op.create_table(
            "export_artifacts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("export_id", sa.String(36), sa.ForeignKey("exports.id"), nullable=False),
            sa.Column("artifact_type", sa.String(40), nullable=False),
            sa.Column("language", sa.String(24), nullable=False),
            sa.Column("asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False),
            sa.Column("reused_from_asset_id", sa.String(36), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("export_id", "artifact_type", "language"),
        )
        for column in ("export_id", "artifact_type", "asset_id"):
            op.create_index(f"ix_export_artifacts_{column}", "export_artifacts", [column])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("export_artifacts", "rights_preflights", "export_profiles"):
        if table in tables:
            op.drop_table(table)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("exports")}
    for column in ("language", "export_profile_id"):
        name = f"ix_exports_{column}"
        if name in indexes:
            op.drop_index(name, table_name="exports")
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("exports")}
    with op.batch_alter_table("exports") as batch:
        for column in (
            "qc_report_asset_id",
            "stems_manifest_asset_id",
            "cover_asset_id",
            "picture_master_asset_id",
            "rights_preflight_id",
            "language",
            "export_profile_id",
        ):
            if column in columns:
                batch.drop_column(column)
