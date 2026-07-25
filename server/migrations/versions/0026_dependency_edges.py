"""Persist dependency edges captured by change impact analysis.

Revision ID: 0026_dependency_edges
Revises: 0025_script_excerpt_revisions
Create Date: 2026-07-26
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "0026_dependency_edges"
down_revision: str | None = "0025_script_excerpt_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _edge_id(change_set_id: str, index: int, edge: dict[str, object]) -> str:
    canonical = json.dumps(edge, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(uuid5(NAMESPACE_URL, f"dependency-edge:{change_set_id}:{index}:{canonical}"))


def _reference(value: object) -> tuple[str, str, str | None] | None:
    if not isinstance(value, dict):
        return None
    object_type = value.get("type")
    object_id = value.get("id")
    version_id = value.get("version_id")
    if not isinstance(object_type, str) or not isinstance(object_id, str):
        return None
    return (
        object_type,
        object_id,
        version_id if isinstance(version_id, str) else None,
    )


def _dependency_edges(value: object) -> list[dict[str, object]]:
    if not isinstance(value, dict):
        return []
    nested = value.get("impact")
    impact = nested if isinstance(nested, dict) else value
    edges = impact.get("dependency_edges")
    if not isinstance(edges, list):
        return []
    return [edge for edge in edges if isinstance(edge, dict)]


def _backfill() -> None:
    bind = op.get_bind()
    change_sets = sa.table(
        "change_sets",
        sa.column("id", sa.String(36)),
        sa.column("project_id", sa.String(36)),
        sa.column("impact_json", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    dependency_edges = sa.table(
        "dependency_edges",
        sa.column("id", sa.String(36)),
        sa.column("project_id", sa.String(36)),
        sa.column("change_set_id", sa.String(36)),
        sa.column("source_type", sa.String(48)),
        sa.column("source_id", sa.String(36)),
        sa.column("source_version_id", sa.String(36)),
        sa.column("target_type", sa.String(48)),
        sa.column("target_id", sa.String(36)),
        sa.column("target_version_id", sa.String(36)),
        sa.column("relation", sa.String(64)),
        sa.column("evidence", sa.Text()),
        sa.column("inferred", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    for row in bind.execute(
        sa.select(
            change_sets.c.id,
            change_sets.c.project_id,
            change_sets.c.impact_json,
            change_sets.c.created_at,
        )
    ).mappings():
        try:
            impact = json.loads(row["impact_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        records: list[dict[str, object]] = []
        seen: set[tuple[object, ...]] = set()
        for index, edge in enumerate(_dependency_edges(impact)):
            source = _reference(edge.get("source"))
            target = _reference(edge.get("target"))
            relation = edge.get("relation")
            evidence = edge.get("evidence")
            if (
                source is None
                or target is None
                or not isinstance(relation, str)
                or not isinstance(evidence, str)
            ):
                continue
            key = (*source, *target, relation)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "id": _edge_id(row["id"], index, edge),
                    "project_id": row["project_id"],
                    "change_set_id": row["id"],
                    "source_type": source[0],
                    "source_id": source[1],
                    "source_version_id": source[2],
                    "target_type": target[0],
                    "target_id": target[1],
                    "target_version_id": target[2],
                    "relation": relation,
                    "evidence": evidence,
                    "inferred": bool(edge.get("inferred", False)),
                    "created_at": row["created_at"] or datetime.now(UTC),
                }
            )
        if records:
            bind.execute(dependency_edges.insert(), records)


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "dependency_edges" in tables:
        return
    op.create_table(
        "dependency_edges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "change_set_id",
            sa.String(36),
            sa.ForeignKey("change_sets.id"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_version_id", sa.String(36), nullable=True),
        sa.Column("target_type", sa.String(48), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("target_version_id", sa.String(36), nullable=True),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("inferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "change_set_id",
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "relation",
            name="uq_dependency_edges_change_set_lineage",
        ),
    )
    op.create_index(
        "ix_dependency_edges_project_source",
        "dependency_edges",
        ["project_id", "source_type", "source_id"],
    )
    op.create_index(
        "ix_dependency_edges_project_target",
        "dependency_edges",
        ["project_id", "target_type", "target_id"],
    )
    op.create_index(
        "ix_dependency_edges_change_set_id",
        "dependency_edges",
        ["change_set_id"],
    )
    _backfill()


def downgrade() -> None:
    if "dependency_edges" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.drop_index("ix_dependency_edges_change_set_id", table_name="dependency_edges")
    op.drop_index("ix_dependency_edges_project_target", table_name="dependency_edges")
    op.drop_index("ix_dependency_edges_project_source", table_name="dependency_edges")
    op.drop_table("dependency_edges")
