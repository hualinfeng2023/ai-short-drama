"""Add multi-track timeline clips, stems, and whole-film QC.

Revision ID: 0017_multitrack_timeline
Revises: 0016_audio_pipeline
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_multitrack_timeline"
down_revision: str | None = "0016_audio_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("timeline_versions")}
    with op.batch_alter_table("timeline_versions") as batch:
        if "stems_manifest_asset_id" not in columns:
            batch.add_column(sa.Column("stems_manifest_asset_id", sa.String(36), nullable=True))
        if "qc_report_asset_id" not in columns:
            batch.add_column(sa.Column("qc_report_asset_id", sa.String(36), nullable=True))

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "timeline_tracks" not in tables:
        op.create_table(
            "timeline_tracks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "timeline_id",
                sa.String(36),
                sa.ForeignKey("timeline_versions.id"),
                nullable=False,
            ),
            sa.Column("track_type", sa.String(32), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("gain_db", sa.Float(), nullable=False),
            sa.Column("stem_asset_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("timeline_id", "track_type", "ordinal"),
        )
        op.create_index("ix_timeline_tracks_timeline_id", "timeline_tracks", ["timeline_id"])
        op.create_index("ix_timeline_tracks_track_type", "timeline_tracks", ["track_type"])
        op.create_index("ix_timeline_tracks_status", "timeline_tracks", ["status"])
    if "timeline_clips" not in tables:
        op.create_table(
            "timeline_clips",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "timeline_id",
                sa.String(36),
                sa.ForeignKey("timeline_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "track_id",
                sa.String(36),
                sa.ForeignKey("timeline_tracks.id"),
                nullable=False,
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("source_entity_type", sa.String(48), nullable=False),
            sa.Column("source_entity_id", sa.String(36), nullable=False),
            sa.Column("asset_id", sa.String(36), nullable=True),
            sa.Column("start_ms", sa.Integer(), nullable=False),
            sa.Column("end_ms", sa.Integer(), nullable=False),
            sa.Column("source_in_ms", sa.Integer(), nullable=False),
            sa.Column("source_out_ms", sa.Integer(), nullable=False),
            sa.Column("gain_db", sa.Float(), nullable=False),
            sa.Column("transition_json", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("degraded", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("track_id", "ordinal"),
        )
        for column in (
            "project_id",
            "timeline_id",
            "track_id",
            "source_entity_id",
            "content_hash",
        ):
            op.create_index(f"ix_timeline_clips_{column}", "timeline_clips", [column])
    if "whole_film_quality_checks" not in tables:
        op.create_table(
            "whole_film_quality_checks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "timeline_id",
                sa.String(36),
                sa.ForeignKey("timeline_versions.id"),
                nullable=False,
            ),
            sa.Column("check_type", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("findings_json", sa.Text(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("timeline_id", "check_type"),
        )
        op.create_index(
            "ix_whole_film_quality_checks_project_id",
            "whole_film_quality_checks",
            ["project_id"],
        )
        op.create_index(
            "ix_whole_film_quality_checks_timeline_id",
            "whole_film_quality_checks",
            ["timeline_id"],
        )
        op.create_index(
            "ix_whole_film_quality_checks_status",
            "whole_film_quality_checks",
            ["status"],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in (
        "whole_film_quality_checks",
        "timeline_clips",
        "timeline_tracks",
    ):
        if table in tables:
            op.drop_table(table)
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("timeline_versions")}
    with op.batch_alter_table("timeline_versions") as batch:
        for column in ("qc_report_asset_id", "stems_manifest_asset_id"):
            if column in columns:
                batch.drop_column(column)
