from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from app.schemas import (
    LegacyRelationshipAdapterResult,
    LegacyRelationshipSummary,
    RelationshipEdgePayload,
    RelationshipGraphPayload,
    RelationshipGraphValidationIssue,
    RelationshipPerspectivePayload,
)


def canonical_character_pair_key(source_character_key: str, target_character_key: str) -> str:
    return "|".join(sorted((source_character_key, target_character_key)))


def _relationship_key(source_character_key: str, target_character_key: str) -> str:
    pair = canonical_character_pair_key(source_character_key, target_character_key)
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "-", pair).strip("-")
    if not normalized:
        normalized = "relationship"
    if len(normalized) <= 80:
        return normalized
    digest = sha256(pair.encode()).hexdigest()[:10]
    return f"{normalized[:69]}-{digest}"


def _legacy_characters(story_bible_payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    raw_characters = story_bible_payload.get("characters")
    if not isinstance(raw_characters, list):
        return []
    characters: list[tuple[str, str]] = []
    for item in raw_characters:
        if not isinstance(item, Mapping):
            continue
        character_key = item.get("key")
        name = item.get("name")
        if isinstance(character_key, str) and character_key.strip() and isinstance(name, str):
            if name.strip():
                characters.append((character_key.strip(), name.strip()))
    return characters


def adapt_legacy_relationships(
    story_bible_payload: Mapping[str, Any],
) -> LegacyRelationshipAdapterResult:
    raw_relationships = story_bible_payload.get("relationships")
    if not isinstance(raw_relationships, list) or not raw_relationships:
        return LegacyRelationshipAdapterResult(
            status="EMPTY",
            summaries=[],
            can_create_draft=False,
        )

    characters = _legacy_characters(story_bible_payload)
    name_counts: dict[str, int] = {}
    for _, name in characters:
        name_counts[name] = name_counts.get(name, 0) + 1
    uniquely_named_characters = [item for item in characters if name_counts[item[1]] == 1]
    summaries: list[LegacyRelationshipSummary] = []
    seen_pairs: set[str] = set()
    mapped_count = 0
    for raw_item in raw_relationships:
        if not isinstance(raw_item, str) or not raw_item.strip():
            summaries.append(
                LegacyRelationshipSummary(
                    raw_text=str(raw_item),
                    status="UNMAPPED",
                    reason="旧版关系不是有效文本",
                )
            )
            continue
        raw_text = raw_item.strip()
        if len(raw_text) > 2000:
            summaries.append(
                LegacyRelationshipSummary(
                    raw_text=raw_text,
                    status="UNMAPPED",
                    reason="旧版关系文本过长，必须人工整理后转换",
                )
            )
            continue
        matches = [
            (raw_text.find(name), character_key, name)
            for character_key, name in uniquely_named_characters
            if name in raw_text
        ]
        matches.sort(key=lambda item: (item[0], item[1]))
        unique_matches: list[tuple[int, str, str]] = []
        seen_character_keys: set[str] = set()
        for match in matches:
            if match[1] not in seen_character_keys:
                seen_character_keys.add(match[1])
                unique_matches.append(match)
        if len(unique_matches) != 2:
            summaries.append(
                LegacyRelationshipSummary(
                    raw_text=raw_text,
                    status="UNMAPPED",
                    reason="必须恰好识别出两个不同角色才能安全转换",
                )
            )
            continue

        source_key = unique_matches[0][1]
        target_key = unique_matches[1][1]
        pair_key = canonical_character_pair_key(source_key, target_key)
        if pair_key in seen_pairs:
            summaries.append(
                LegacyRelationshipSummary(
                    raw_text=raw_text,
                    status="UNMAPPED",
                    reason="同一角色对存在多条自由文本，必须人工合并",
                )
            )
            continue
        seen_pairs.add(pair_key)
        mapped_count += 1
        summaries.append(
            LegacyRelationshipSummary(
                raw_text=raw_text,
                status="MAPPED",
                source_character_key=source_key,
                target_character_key=target_key,
                relationship_key=_relationship_key(source_key, target_key),
            )
        )

    if mapped_count == len(summaries):
        status = "MAPPED"
    elif mapped_count:
        status = "PARTIAL"
    else:
        status = "UNMAPPED"
    return LegacyRelationshipAdapterResult(
        status=status,
        summaries=summaries,
        can_create_draft=status == "MAPPED" and bool(summaries),
    )


def build_legacy_relationship_graph(
    story_bible_payload: Mapping[str, Any],
) -> RelationshipGraphPayload | None:
    adapted = adapt_legacy_relationships(story_bible_payload)
    if not adapted.can_create_draft:
        return None
    edges = []
    for ordinal, summary in enumerate(adapted.summaries, start=1):
        if (
            summary.source_character_key is None
            or summary.target_character_key is None
            or summary.relationship_key is None
        ):
            return None
        edges.append(
            RelationshipEdgePayload(
                relationship_key=summary.relationship_key,
                source_character_key=summary.source_character_key,
                target_character_key=summary.target_character_key,
                directionality="BIDIRECTIONAL",
                relationship_types=["OTHER"],
                surface_relationship=summary.raw_text,
                true_relationship=summary.raw_text,
                source_view=RelationshipPerspectivePayload(
                    perceived_relationship=summary.raw_text,
                    belief=summary.raw_text,
                ),
                target_view=RelationshipPerspectivePayload(
                    perceived_relationship=summary.raw_text,
                    belief=summary.raw_text,
                ),
                trust_level=0,
                emotional_temperature=0,
                power_balance=0,
                conflict_intensity=0,
                story_function=f"旧版关系说明：{summary.raw_text}",
                is_core=False,
                locked=False,
                ordinal=ordinal,
            )
        )
    return RelationshipGraphPayload(
        edges=edges,
        generation_notes=["由旧版关系文本安全映射，必须人工补充核心关系与变化事件。"],
    )


def validate_relationship_graph(
    payload: RelationshipGraphPayload,
    story_bible_payload: Mapping[str, Any],
) -> list[RelationshipGraphValidationIssue]:
    characters = _legacy_characters(story_bible_payload)
    character_keys = {item[0] for item in characters}
    raw_characters = story_bible_payload.get("characters")
    if not isinstance(raw_characters, list):
        raw_characters = []
    character_roles = {
        str(item.get("key")): str(item.get("role", ""))
        for item in raw_characters
        if isinstance(item, Mapping) and isinstance(item.get("key"), str)
    }
    issues: list[RelationshipGraphValidationIssue] = []
    connected_keys: set[str] = set()
    for edge in payload.edges:
        missing_keys = {
            edge.source_character_key,
            edge.target_character_key,
        } - character_keys
        for missing_key in sorted(missing_keys):
            issues.append(
                RelationshipGraphValidationIssue(
                    severity="BLOCKER",
                    code="INVALID_CHARACTER_REFERENCE",
                    message=f"关系 {edge.relationship_key} 引用了不存在的角色 {missing_key}。",
                    relationship_key=edge.relationship_key,
                    character_key=missing_key,
                )
            )
        if not missing_keys:
            connected_keys.update((edge.source_character_key, edge.target_character_key))
        if "FAMILY" in edge.relationship_types and edge.family_kinship is None:
            issues.append(
                RelationshipGraphValidationIssue(
                    severity="WARNING",
                    code="FAMILY_KINSHIP_UNSPECIFIED",
                    message=(
                        f"亲属关系 {edge.relationship_key} 尚未标记血缘来源；"
                        "系统不会自动添加容貌相似约束。"
                    ),
                    relationship_key=edge.relationship_key,
                )
            )

    core_character_keys = {
        key
        for key, role in character_roles.items()
        if role.upper() in {"PROTAGONIST", "LEAD", "CORE"}
    }
    for character_key in sorted(core_character_keys - connected_keys):
        issues.append(
            RelationshipGraphValidationIssue(
                severity="BLOCKER",
                code="CORE_CHARACTER_ISOLATED",
                message=f"核心角色 {character_key} 没有任何有效关系。",
                character_key=character_key,
            )
        )

    if not payload.core_relationship_keys:
        issues.append(
            RelationshipGraphValidationIssue(
                severity="BLOCKER",
                code="MISSING_CORE_RELATIONSHIP",
                message="至少需要指定一条核心关系。",
            )
        )

    conflict_relationships = [
        edge
        for edge in payload.edges
        if edge.conflict_intensity >= 2 or bool({"RIVAL", "CONTROL"} & set(edge.relationship_types))
    ]
    if not conflict_relationships:
        issues.append(
            RelationshipGraphValidationIssue(
                severity="BLOCKER",
                code="MISSING_PRIMARY_CONFLICT",
                message="至少需要一条具有明确冲突的角色关系。",
            )
        )

    if not payload.beats:
        issues.append(
            RelationshipGraphValidationIssue(
                severity="BLOCKER",
                code="MISSING_RELATIONSHIP_BEAT",
                message="至少需要一个具有事件、证据和前后状态的关系变化。",
            )
        )

    beats_by_relationship: dict[str, list[str]] = {}
    for beat in payload.beats:
        beats_by_relationship.setdefault(beat.relationship_key, []).append(beat.trigger_type)
    for edge in payload.edges:
        trigger_types = set(beats_by_relationship.get(edge.relationship_key, []))
        if edge.surface_relationship != edge.true_relationship and not (
            {"REVEAL", "AUTHENTICATION"} & trigger_types
        ):
            issues.append(
                RelationshipGraphValidationIssue(
                    severity="BLOCKER",
                    code="HIDDEN_RELATIONSHIP_WITHOUT_REVEAL",
                    message=f"关系 {edge.relationship_key} 的明面与真相不同，但没有揭示计划。",
                    relationship_key=edge.relationship_key,
                )
            )
        if edge.is_core and not trigger_types:
            issues.append(
                RelationshipGraphValidationIssue(
                    severity="WARNING",
                    code="CORE_RELATIONSHIP_WITHOUT_BEAT",
                    message=f"核心关系 {edge.relationship_key} 本集没有关系变化。",
                    relationship_key=edge.relationship_key,
                )
            )
    return issues


def relationship_graph_has_blockers(issues: list[RelationshipGraphValidationIssue]) -> bool:
    return any(item.severity == "BLOCKER" for item in issues)
