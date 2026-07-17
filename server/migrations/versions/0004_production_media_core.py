"""Add story, character, asset, timeline, revision, and export core.

Revision ID: 0004_production_media_core
Revises: 0003_persistent_jobs_events
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_production_media_core"
down_revision: str | None = "0003_persistent_jobs_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns(table)}
    if column.name not in columns:
        op.add_column(table, column)


def create_index_if_missing(table: str, name: str, columns: list[str]) -> None:
    bind = op.get_bind()
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def upgrade() -> None:
    add_column_if_missing(
        "projects", sa.Column("current_story_version_id", sa.String(36), nullable=True)
    )
    add_column_if_missing(
        "projects", sa.Column("current_timeline_version_id", sa.String(36), nullable=True)
    )
    add_column_if_missing(
        "episodes", sa.Column("ordinal", sa.Integer(), server_default="1", nullable=False)
    )
    add_column_if_missing("shots", sa.Column("current_take_id", sa.String(36), nullable=True))
    add_column_if_missing(
        "shots", sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False)
    )

    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "story_versions" not in tables:
        op.create_table(
            "story_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("proposal_version", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(160), nullable=False),
            sa.Column("logline", sa.Text(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_by", sa.String(80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "version"),
        )
        op.create_index("ix_story_versions_project_id", "story_versions", ["project_id"])
        op.create_index("ix_story_versions_status", "story_versions", ["status"])

    tables = set(sa.inspect(bind).get_table_names())
    if "assets" not in tables:
        op.create_table(
            "assets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("kind", sa.String(48), nullable=False),
            sa.Column("storage_key", sa.String(320), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("mime", sa.String(100), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("provider", sa.String(48), nullable=False),
            sa.Column("is_temporary", sa.Boolean(), nullable=False),
            sa.Column("width", sa.Integer(), nullable=True),
            sa.Column("height", sa.Integer(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("source_entity_type", sa.String(48), nullable=False),
            sa.Column("source_entity_id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "sha256", "kind"),
        )
        op.create_index("ix_assets_project_id", "assets", ["project_id"])
        op.create_index("ix_assets_kind", "assets", ["kind"])
        op.create_index("ix_assets_sha256", "assets", ["sha256"])
        op.create_index("ix_assets_status", "assets", ["status"])
        op.create_index("ix_assets_source_entity_id", "assets", ["source_entity_id"])

    tables = set(sa.inspect(bind).get_table_names())
    if "characters" not in tables:
        op.create_table(
            "characters",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("character_key", sa.String(80), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("role", sa.String(40), nullable=False),
            sa.Column("visual_brief", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("locked_candidate_id", sa.String(36), nullable=True),
            sa.Column("lock_version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "character_key"),
        )
        op.create_index("ix_characters_project_id", "characters", ["project_id"])
        op.create_index("ix_characters_status", "characters", ["status"])

    tables = set(sa.inspect(bind).get_table_names())
    if "character_candidates" not in tables:
        op.create_table(
            "character_candidates",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "character_id", sa.String(36), sa.ForeignKey("characters.id"), nullable=False
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False),
            sa.Column("seed", sa.String(80), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("selected", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("character_id", "ordinal"),
        )
        op.create_index(
            "ix_character_candidates_project_id", "character_candidates", ["project_id"]
        )
        op.create_index(
            "ix_character_candidates_character_id", "character_candidates", ["character_id"]
        )
        op.create_index("ix_character_candidates_status", "character_candidates", ["status"])

    tables = set(sa.inspect(bind).get_table_names())
    if "takes" not in tables:
        op.create_table(
            "takes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("shot_id", sa.String(36), sa.ForeignKey("shots.id"), nullable=False),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("approval", sa.String(32), nullable=False),
            sa.Column("is_current", sa.Boolean(), nullable=False),
            sa.Column("parent_take_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("shot_id", "kind", "version"),
        )
        op.create_index("ix_takes_shot_id", "takes", ["shot_id"])
        op.create_index("ix_takes_status", "takes", ["status"])
        op.create_index("ix_takes_approval", "takes", ["approval"])
        op.create_index(
            "ix_takes_one_current_per_shot",
            "takes",
            ["shot_id"],
            unique=True,
            sqlite_where=sa.text("is_current = 1"),
        )

    tables = set(sa.inspect(bind).get_table_names())
    if "timeline_versions" not in tables:
        op.create_table(
            "timeline_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("episode_id", sa.String(36), sa.ForeignKey("episodes.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("mp4_asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False),
            sa.Column("srt_asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False),
            sa.Column("vtt_asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False),
            sa.Column(
                "manifest_asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False
            ),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("baseline_hash", sa.String(64), nullable=False),
            sa.Column("parent_timeline_id", sa.String(36), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "version"),
        )
        op.create_index("ix_timeline_versions_project_id", "timeline_versions", ["project_id"])
        op.create_index("ix_timeline_versions_episode_id", "timeline_versions", ["episode_id"])
        op.create_index("ix_timeline_versions_status", "timeline_versions", ["status"])

    tables = set(sa.inspect(bind).get_table_names())
    if "timeline_items" not in tables:
        op.create_table(
            "timeline_items",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "timeline_id", sa.String(36), sa.ForeignKey("timeline_versions.id"), nullable=False
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("shot_id", sa.String(36), sa.ForeignKey("shots.id"), nullable=False),
            sa.Column("take_id", sa.String(36), sa.ForeignKey("takes.id"), nullable=False),
            sa.Column("start_ms", sa.Integer(), nullable=False),
            sa.Column("end_ms", sa.Integer(), nullable=False),
            sa.UniqueConstraint("timeline_id", "ordinal"),
        )
        op.create_index("ix_timeline_items_timeline_id", "timeline_items", ["timeline_id"])
        op.create_index("ix_timeline_items_shot_id", "timeline_items", ["shot_id"])

    tables = set(sa.inspect(bind).get_table_names())
    if "change_sets" not in tables:
        op.create_table(
            "change_sets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "base_timeline_id",
                sa.String(36),
                sa.ForeignKey("timeline_versions.id"),
                nullable=False,
            ),
            sa.Column("scope_json", sa.Text(), nullable=False),
            sa.Column("instruction", sa.Text(), nullable=False),
            sa.Column("impact_json", sa.Text(), nullable=False),
            sa.Column("estimate_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("result_timeline_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_change_sets_project_id", "change_sets", ["project_id"])
        op.create_index("ix_change_sets_status", "change_sets", ["status"])

    tables = set(sa.inspect(bind).get_table_names())
    if "exports" not in tables:
        op.create_table(
            "exports",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "timeline_id", sa.String(36), sa.ForeignKey("timeline_versions.id"), nullable=False
            ),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("profile", sa.String(48), nullable=False),
            sa.Column("mp4_asset_id", sa.String(36), nullable=True),
            sa.Column("srt_asset_id", sa.String(36), nullable=True),
            sa.Column("vtt_asset_id", sa.String(36), nullable=True),
            sa.Column("manifest_asset_id", sa.String(36), nullable=True),
            sa.Column("rights_status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_exports_project_id", "exports", ["project_id"])
        op.create_index("ix_exports_status", "exports", ["status"])

    tables = set(sa.inspect(bind).get_table_names())
    if "usage_ledger" not in tables:
        op.create_table(
            "usage_ledger",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True),
            sa.Column("entry_type", sa.String(32), nullable=False),
            sa.Column("points", sa.Integer(), nullable=False),
            sa.Column("description", sa.String(200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_usage_ledger_project_id", "usage_ledger", ["project_id"])
        op.create_index("ix_usage_ledger_entry_type", "usage_ledger", ["entry_type"])

    tables = set(sa.inspect(bind).get_table_names())
    if "audit_log" not in tables:
        op.create_table(
            "audit_log",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("actor", sa.String(80), nullable=False),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("entity_type", sa.String(48), nullable=False),
            sa.Column("entity_id", sa.String(36), nullable=False),
            sa.Column("before_hash", sa.String(64), nullable=True),
            sa.Column("after_hash", sa.String(64), nullable=True),
            sa.Column("trace_id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_audit_log_project_id", "audit_log", ["project_id"])
        op.create_index("ix_audit_log_action", "audit_log", ["action"])


def downgrade() -> None:
    for table in (
        "audit_log",
        "usage_ledger",
        "exports",
        "change_sets",
        "timeline_items",
        "timeline_versions",
        "takes",
        "character_candidates",
        "characters",
        "assets",
        "story_versions",
    ):
        op.drop_table(table)
    with op.batch_alter_table("shots") as batch:
        batch.drop_column("lock_version")
        batch.drop_column("current_take_id")
    with op.batch_alter_table("episodes") as batch:
        batch.drop_column("ordinal")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("current_timeline_version_id")
        batch.drop_column("current_story_version_id")
