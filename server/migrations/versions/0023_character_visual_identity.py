# ruff: noqa: E501
"""Add character visual profile, identity and downstream version snapshots.

Revision ID: 0023_character_visual_identity
Revises: 0022_narrative_targeting
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_character_visual_identity"
down_revision: str | None = "0022_narrative_targeting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "character_visual_profile_versions" not in tables:
        op.create_table(
            "character_visual_profile_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "character_id", sa.String(36), sa.ForeignKey("characters.id"), nullable=False
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column(
                "source_story_bible_version_id",
                sa.String(36),
                sa.ForeignKey("story_bible_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "source_relationship_graph_id",
                sa.String(36),
                sa.ForeignKey("relationship_graph_versions.id"),
                nullable=False,
            ),
            sa.Column("identity_fields_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("appearance_fields_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column(
                "personality_visualization_json", sa.Text(), nullable=False, server_default="{}"
            ),
            sa.Column("styling_fields_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("project_style_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("negative_constraints_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("conflict_report_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column(
                "recommended_directions_json", sa.Text(), nullable=False, server_default="[]"
            ),
            sa.Column("selected_direction", sa.String(120), nullable=True),
            sa.Column("source_content_hash", sa.String(64), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confirmed_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("character_id", "version"),
        )
        for column in (
            "project_id",
            "character_id",
            "source_story_bible_version_id",
            "source_relationship_graph_id",
            "status",
        ):
            op.create_index(
                f"ix_character_visual_profile_versions_{column}",
                "character_visual_profile_versions",
                [column],
            )

    if "character_candidate_batches" not in tables:
        op.create_table(
            "character_candidate_batches",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "character_id", sa.String(36), sa.ForeignKey("characters.id"), nullable=False
            ),
            sa.Column(
                "profile_version_id",
                sa.String(36),
                sa.ForeignKey("character_visual_profile_versions.id"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("requested_count", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("composition", sa.String(40), nullable=False, server_default="FRONT_BUST"),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("prompt_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("character_id", "version"),
        )
        for column in ("project_id", "character_id", "profile_version_id", "status"):
            op.create_index(
                f"ix_character_candidate_batches_{column}", "character_candidate_batches", [column]
            )

    if "character_identity_versions" not in tables:
        op.create_table(
            "character_identity_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "character_id", sa.String(36), sa.ForeignKey("characters.id"), nullable=False
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column(
                "source_candidate_id",
                sa.String(36),
                sa.ForeignKey("character_candidates.id"),
                nullable=False,
            ),
            sa.Column(
                "profile_version_id",
                sa.String(36),
                sa.ForeignKey("character_visual_profile_versions.id"),
                nullable=False,
            ),
            sa.Column("stable_traits_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("prompt_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("character_id", "version"),
        )
        for column in (
            "project_id",
            "character_id",
            "source_candidate_id",
            "profile_version_id",
            "status",
        ):
            op.create_index(
                f"ix_character_identity_versions_{column}", "character_identity_versions", [column]
            )

    if "character_identity_assets" not in tables:
        op.create_table(
            "character_identity_assets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "character_id", sa.String(36), sa.ForeignKey("characters.id"), nullable=False
            ),
            sa.Column(
                "identity_version_id",
                sa.String(36),
                sa.ForeignKey("character_identity_versions.id"),
                nullable=False,
            ),
            sa.Column("view_type", sa.String(40), nullable=False),
            sa.Column("asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("identity_version_id", "view_type"),
        )
        for column in ("project_id", "character_id", "identity_version_id", "asset_id", "status"):
            op.create_index(
                f"ix_character_identity_assets_{column}", "character_identity_assets", [column]
            )

    if "character_story_state_versions" not in tables:
        op.create_table(
            "character_story_state_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column(
                "character_id", sa.String(36), sa.ForeignKey("characters.id"), nullable=False
            ),
            sa.Column(
                "identity_version_id",
                sa.String(36),
                sa.ForeignKey("character_identity_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "look_version_id",
                sa.String(36),
                sa.ForeignKey("character_look_versions.id"),
                nullable=True,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("label", sa.String(120), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("character_id", "version"),
        )
        for column in (
            "project_id",
            "character_id",
            "identity_version_id",
            "look_version_id",
            "status",
        ):
            op.create_index(
                f"ix_character_story_state_versions_{column}",
                "character_story_state_versions",
                [column],
            )

    character_columns = _columns("characters")
    for name in (
        "source_story_bible_version_id",
        "source_relationship_graph_id",
        "current_profile_version_id",
        "locked_identity_version_id",
        "active_look_version_id",
        "active_story_state_version_id",
    ):
        if name not in character_columns:
            op.add_column("characters", sa.Column(name, sa.String(36), nullable=True))
    if "source_story_bible_version_id" not in character_columns:
        op.create_index(
            "ix_characters_source_story_bible_version_id",
            "characters",
            ["source_story_bible_version_id"],
        )
    if "source_relationship_graph_id" not in character_columns:
        op.create_index(
            "ix_characters_source_relationship_graph_id",
            "characters",
            ["source_relationship_graph_id"],
        )

    candidate_columns = _columns("character_candidates")
    with op.batch_alter_table("character_candidates") as batch:
        if "batch_id" not in candidate_columns:
            batch.add_column(sa.Column("batch_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_character_candidates_batch_id",
                "character_candidate_batches",
                ["batch_id"],
                ["id"],
            )
            batch.create_index("ix_character_candidates_batch_id", ["batch_id"])
        if "profile_version_id" not in candidate_columns:
            batch.add_column(sa.Column("profile_version_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_character_candidates_profile_version_id",
                "character_visual_profile_versions",
                ["profile_version_id"],
                ["id"],
            )
            batch.create_index("ix_character_candidates_profile_version_id", ["profile_version_id"])
        if "prompt_snapshot_json" not in candidate_columns:
            batch.add_column(
                sa.Column("prompt_snapshot_json", sa.Text(), nullable=False, server_default="{}")
            )
        if "review_status" not in candidate_columns:
            batch.add_column(
                sa.Column(
                    "review_status",
                    sa.String(32),
                    nullable=False,
                    server_default="PENDING_SELECTION",
                )
            )

    look_columns = _columns("character_look_versions")
    with op.batch_alter_table("character_look_versions") as batch:
        if "identity_version_id" not in look_columns:
            batch.add_column(sa.Column("identity_version_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_character_look_versions_identity",
                "character_identity_versions",
                ["identity_version_id"],
                ["id"],
            )
            batch.create_index(
                "ix_character_look_versions_identity_version_id", ["identity_version_id"]
            )
        if "parent_version_id" not in look_columns:
            batch.add_column(sa.Column("parent_version_id", sa.String(36), nullable=True))
        if "change_reason" not in look_columns:
            batch.add_column(
                sa.Column(
                    "change_reason",
                    sa.String(160),
                    nullable=False,
                    server_default="角色身份锁定后的基础造型",
                )
            )

    shot_columns = _columns("shots")
    for name in (
        "character_identity_version_ids_json",
        "character_look_version_ids_json",
        "character_story_state_version_ids_json",
    ):
        if name not in shot_columns:
            op.add_column(
                "shots",
                sa.Column(name, sa.Text(), nullable=False, server_default="[]"),
            )


def downgrade() -> None:
    shot_columns = _columns("shots")
    for name in (
        "character_story_state_version_ids_json",
        "character_look_version_ids_json",
        "character_identity_version_ids_json",
    ):
        if name in shot_columns:
            op.drop_column("shots", name)

    look_columns = _columns("character_look_versions")
    with op.batch_alter_table("character_look_versions") as batch:
        for name in ("change_reason", "parent_version_id", "identity_version_id"):
            if name in look_columns:
                batch.drop_column(name)

    candidate_columns = _columns("character_candidates")
    with op.batch_alter_table("character_candidates") as batch:
        for name in ("review_status", "prompt_snapshot_json", "profile_version_id", "batch_id"):
            if name in candidate_columns:
                batch.drop_column(name)

    character_columns = _columns("characters")
    if "source_story_bible_version_id" in character_columns:
        op.drop_index("ix_characters_source_story_bible_version_id", table_name="characters")
    if "source_relationship_graph_id" in character_columns:
        op.drop_index("ix_characters_source_relationship_graph_id", table_name="characters")
    for name in (
        "active_story_state_version_id",
        "active_look_version_id",
        "locked_identity_version_id",
        "current_profile_version_id",
        "source_relationship_graph_id",
        "source_story_bible_version_id",
    ):
        if name in character_columns:
            op.drop_column("characters", name)

    for table in (
        "character_identity_assets",
        "character_story_state_versions",
        "character_identity_versions",
        "character_candidate_batches",
        "character_visual_profile_versions",
    ):
        if table in _tables():
            op.drop_table(table)
