"""Persist shot character bindings and take identity quality control.

Revision ID: 0006_character_consistency
Revises: 0005_reference_asset_metadata
Create Date: 2026-07-14
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_character_consistency"
down_revision: str | None = "0005_reference_asset_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    shot_columns = _columns("shots")
    with op.batch_alter_table("shots") as batch:
        if "character_ids_json" not in shot_columns:
            batch.add_column(
                sa.Column("character_ids_json", sa.Text(), server_default="[]", nullable=False)
            )
        if "character_look_version" not in shot_columns:
            batch.add_column(
                sa.Column(
                    "character_look_version",
                    sa.String(40),
                    server_default="Look V1",
                    nullable=False,
                )
            )

    take_columns = _columns("takes")
    with op.batch_alter_table("takes") as batch:
        if "identity_status" not in take_columns:
            batch.add_column(
                sa.Column(
                    "identity_status",
                    sa.String(32),
                    server_default="NOT_APPLICABLE",
                    nullable=False,
                )
            )
        if "identity_score" not in take_columns:
            batch.add_column(sa.Column("identity_score", sa.Float(), nullable=True))
        if "identity_message" not in take_columns:
            batch.add_column(sa.Column("identity_message", sa.Text(), nullable=True))
        if "identity_reference_asset_ids_json" not in take_columns:
            batch.add_column(
                sa.Column(
                    "identity_reference_asset_ids_json",
                    sa.Text(),
                    server_default="[]",
                    nullable=False,
                )
            )

    # Existing production projects may already have a locked protagonist. Bind that
    # canonical identity to their unbound shots without inventing any new character.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT shots.id AS shot_id, characters.id AS character_id
            FROM shots
            JOIN scenes ON scenes.id = shots.scene_id
            JOIN episodes ON episodes.id = scenes.episode_id
            JOIN characters ON characters.project_id = episodes.project_id
            WHERE characters.locked_candidate_id IS NOT NULL
            ORDER BY shots.id, characters.role, characters.id
            """
        )
    ).mappings()
    bindings: dict[str, list[str]] = {}
    for row in rows:
        bindings.setdefault(str(row["shot_id"]), []).append(str(row["character_id"]))
    for shot_id, character_ids in bindings.items():
        bind.execute(
            sa.text(
                "UPDATE shots SET character_ids_json = :character_ids "
                "WHERE id = :shot_id AND character_ids_json = '[]'"
            ),
            {"shot_id": shot_id, "character_ids": json.dumps(character_ids)},
        )


def downgrade() -> None:
    take_columns = _columns("takes")
    with op.batch_alter_table("takes") as batch:
        for column in (
            "identity_reference_asset_ids_json",
            "identity_message",
            "identity_score",
            "identity_status",
        ):
            if column in take_columns:
                batch.drop_column(column)
    shot_columns = _columns("shots")
    with op.batch_alter_table("shots") as batch:
        for column in ("character_look_version", "character_ids_json"):
            if column in shot_columns:
                batch.drop_column(column)
