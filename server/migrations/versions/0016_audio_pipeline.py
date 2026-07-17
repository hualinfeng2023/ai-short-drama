"""Add sound briefs, audio cues/takes, and lip-sync takes.

Revision ID: 0016_audio_pipeline
Revises: 0015_generation_qc_review
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_audio_pipeline"
down_revision: str | None = "0015_generation_qc_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sound_brief_versions" not in tables:
        op.create_table(
            "sound_brief_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "storyboard_version_id",
                sa.String(36),
                sa.ForeignKey("storyboard_versions.id"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("rights_status", sa.String(32), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "version"),
        )
        op.create_index(
            "ix_sound_brief_versions_project_id", "sound_brief_versions", ["project_id"]
        )
        op.create_index(
            "ix_sound_brief_versions_storyboard_version_id",
            "sound_brief_versions",
            ["storyboard_version_id"],
        )
        op.create_index("ix_sound_brief_versions_status", "sound_brief_versions", ["status"])
    if "audio_cues" not in tables:
        op.create_table(
            "audio_cues",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "storyboard_version_id",
                sa.String(36),
                sa.ForeignKey("storyboard_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "script_line_id", sa.String(36), sa.ForeignKey("script_lines.id"), nullable=True
            ),
            sa.Column(
                "script_scene_id",
                sa.String(36),
                sa.ForeignKey("script_scenes.id"),
                nullable=True,
            ),
            sa.Column("shot_id", sa.String(36), sa.ForeignKey("shots.id"), nullable=True),
            sa.Column(
                "voice_profile_id",
                sa.String(36),
                sa.ForeignKey("voice_profiles.id"),
                nullable=True,
            ),
            sa.Column("cue_type", sa.String(32), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("start_ms", sa.Integer(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in (
            "project_id",
            "storyboard_version_id",
            "script_line_id",
            "script_scene_id",
            "shot_id",
            "cue_type",
            "status",
        ):
            op.create_index(f"ix_audio_cues_{column}", "audio_cues", [column])
    if "audio_takes" not in tables:
        op.create_table(
            "audio_takes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "audio_cue_id",
                sa.String(36),
                sa.ForeignKey("audio_cues.id"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False),
            sa.Column(
                "generation_record_id",
                sa.String(36),
                sa.ForeignKey("generation_records.id"),
                nullable=True,
            ),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("approval", sa.String(32), nullable=False),
            sa.Column("is_current", sa.Boolean(), nullable=False),
            sa.Column("quality_status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("audio_cue_id", "version"),
        )
        op.create_index("ix_audio_takes_project_id", "audio_takes", ["project_id"])
        op.create_index("ix_audio_takes_audio_cue_id", "audio_takes", ["audio_cue_id"])
        op.create_index(
            "ix_audio_takes_generation_record_id", "audio_takes", ["generation_record_id"]
        )
        op.create_index("ix_audio_takes_status", "audio_takes", ["status"])
        op.create_index("ix_audio_takes_approval", "audio_takes", ["approval"])
        op.create_index(
            "ix_audio_takes_one_current_per_cue",
            "audio_takes",
            ["audio_cue_id"],
            unique=True,
            sqlite_where=sa.text("is_current = 1"),
        )
    if "lip_sync_takes" not in tables:
        op.create_table(
            "lip_sync_takes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("shot_id", sa.String(36), sa.ForeignKey("shots.id"), nullable=False),
            sa.Column("video_take_id", sa.String(36), sa.ForeignKey("takes.id"), nullable=False),
            sa.Column(
                "audio_take_id", sa.String(36), sa.ForeignKey("audio_takes.id"), nullable=False
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("output_asset_id", sa.String(36), nullable=True),
            sa.Column(
                "generation_record_id",
                sa.String(36),
                sa.ForeignKey("generation_records.id"),
                nullable=True,
            ),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("approval", sa.String(32), nullable=False),
            sa.Column("fallback_strategy", sa.String(48), nullable=True),
            sa.Column("quality_status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("shot_id", "version"),
        )
        op.create_index("ix_lip_sync_takes_project_id", "lip_sync_takes", ["project_id"])
        op.create_index("ix_lip_sync_takes_shot_id", "lip_sync_takes", ["shot_id"])
        op.create_index(
            "ix_lip_sync_takes_generation_record_id", "lip_sync_takes", ["generation_record_id"]
        )
        op.create_index("ix_lip_sync_takes_status", "lip_sync_takes", ["status"])
        op.create_index("ix_lip_sync_takes_approval", "lip_sync_takes", ["approval"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("lip_sync_takes", "audio_takes", "audio_cues", "sound_brief_versions"):
        if table in tables:
            op.drop_table(table)
