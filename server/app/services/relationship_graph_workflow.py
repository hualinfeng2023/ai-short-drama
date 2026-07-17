from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ChangeSet,
    EpisodeOutlineVersion,
    Job,
    Project,
    RelationshipBeat,
    RelationshipEdge,
    RelationshipGraphVersion,
    ReviewRecord,
    ScriptScene,
    ScriptVersion,
    StoryBibleVersion,
)
from app.schemas import (
    RelationshipGraphEditability,
    RelationshipGraphPayload,
    RelationshipGraphValidationIssue,
)
from app.services.events import append_event
from app.services.jobs import enqueue_job
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.relationship_graphs import (
    canonical_character_pair_key,
    relationship_graph_has_blockers,
    validate_relationship_graph,
)
from app.services.workspace import project_or_404

ACTIVE_JOB_STATUSES = {"PENDING", "RETRY_WAIT", "RUNNING", "CANCEL_REQUESTED"}
EDITABLE_PROJECT_STATUS = "RELATIONSHIP_READY"
REVISION_PROJECT_STATUSES = {
    "CHARACTER_VISUAL_READY",
    "SCRIPT_PACKAGE_RUNNING",
    "SCRIPT_READY",
    "STORY_APPROVED",
    "PRODUCING",
    "APPROVED",
    "EXPORTED",
}
SCRIPT_PACKAGE_CONFIG_VERSION = "story-package-v5-independent-targeting"


def _http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    user_action: str | None = None,
    details: object | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
            "user_action": user_action,
            "retryable": False,
            "details": details,
        },
    )


def _graph_or_404(session: Session, graph_id: str) -> RelationshipGraphVersion:
    graph = session.get(RelationshipGraphVersion, graph_id)
    if graph is None:
        raise _http_error(
            404,
            "RELATIONSHIP_GRAPH_NOT_FOUND",
            "角色关系网不存在。",
            user_action="返回故事工作区并刷新",
            details={"graph_id": graph_id},
        )
    return graph


def _story_bible_payload(bible: StoryBibleVersion) -> dict[str, object]:
    payload = json.loads(bible.payload_json)
    if not isinstance(payload, dict):
        raise _http_error(
            422,
            "STORY_BIBLE_INVALID",
            "角色设定不是有效对象，无法校验关系网。",
            user_action="重新生成或修复角色设定",
        )
    return payload


def _active_job_type(session: Session, graph_id: str) -> str | None:
    return session.scalar(
        select(Job.job_type).where(
            Job.entity_type == "relationship_graph",
            Job.entity_id == graph_id,
            Job.status.in_(ACTIVE_JOB_STATUSES),
        )
    )


def graph_editability(
    session: Session,
    *,
    project: Project,
    graph: RelationshipGraphVersion,
) -> RelationshipGraphEditability:
    active_job_type = _active_job_type(session, graph.id)
    active_job = active_job_type is not None
    reason_code: str | None = None
    reason_message: str | None = None
    if project.status == "BLOCKED":
        reason_code = "PROJECT_BLOCKED"
        reason_message = "项目处于阻断状态，请先解除阻断。"
    elif project.status == "ARCHIVED":
        reason_code = "PROJECT_ARCHIVED"
        reason_message = "项目已经归档，只能查看历史版本。"
    elif active_job_type == "GENERATE_SCRIPT_PACKAGE":
        reason_code = "SCRIPT_CONSUMING_GRAPH"
        reason_message = "剧本正在使用该关系基线生成，完成后才能发起修订。"
    elif active_job:
        reason_code = "ACTIVE_RELATIONSHIP_JOB"
        reason_message = "关系任务正在执行，完成后才能继续编辑。"
    elif graph.status == "GENERATING":
        reason_code = "GRAPH_GENERATING"
        reason_message = "关系网正在生成，完成后可继续编辑。"
    elif graph.status == "READY_FOR_REVIEW":
        reason_code = "GRAPH_SUBMITTED"
        reason_message = "关系网已经提交审核，语义内容暂时只读。"
    elif graph.status == "APPROVED":
        reason_code = "GRAPH_APPROVED"
        reason_message = "关系网已经批准，请创建修改版后编辑。"
    elif graph.status == "SUPERSEDED":
        reason_code = "GRAPH_SUPERSEDED"
        reason_message = "该关系版本已被替代，只能查看或复制为草稿。"
    elif graph.status == "FAILED":
        reason_code = "GRAPH_FAILED"
        reason_message = "该关系版本生成失败，请重试生成或创建草稿。"
    elif project.status != EDITABLE_PROJECT_STATUS and not (
        graph.parent_version_id is not None and project.status in REVISION_PROJECT_STATUSES
    ):
        reason_code = "PROJECT_EDIT_WINDOW_CLOSED"
        reason_message = "当前项目阶段不开放关系语义编辑。"

    semantic_editable = reason_code is None and graph.status == "DRAFT"
    critic = json.loads(graph.critic_json)
    validation_issues = critic.get("validation_issues", []) if isinstance(critic, dict) else []
    has_blockers = any(
        isinstance(issue, dict) and issue.get("severity") == "BLOCKER"
        for issue in validation_issues
    )
    can_approve = (
        not active_job
        and not has_blockers
        and (
            project.status == EDITABLE_PROJECT_STATUS
            or (graph.parent_version_id is not None and project.status in REVISION_PROJECT_STATUSES)
        )
        and graph.status in {"DRAFT", "READY_FOR_REVIEW"}
    )
    has_open_revision = (
        session.scalar(
            select(RelationshipGraphVersion.id).where(
                RelationshipGraphVersion.project_id == project.id,
                RelationshipGraphVersion.status.in_({"DRAFT", "READY_FOR_REVIEW"}),
                RelationshipGraphVersion.id != graph.id,
            )
        )
        is not None
    )
    return RelationshipGraphEditability(
        semantic_editable=semantic_editable,
        layout_editable=project.status != "ARCHIVED",
        can_submit=semantic_editable,
        can_approve=can_approve,
        can_create_revision=(
            not active_job
            and not has_open_revision
            and project.status in ({EDITABLE_PROJECT_STATUS} | REVISION_PROJECT_STATUSES)
            and graph.status in {"APPROVED", "SUPERSEDED"}
        ),
        active_job=active_job,
        reason_code=reason_code,
        reason_message=reason_message,
        requires_impact_confirmation=project.status in REVISION_PROJECT_STATUSES,
    )


def _graph_payload(session: Session, graph: RelationshipGraphVersion) -> RelationshipGraphPayload:
    edges = session.scalars(
        select(RelationshipEdge)
        .where(RelationshipEdge.graph_version_id == graph.id)
        .order_by(RelationshipEdge.ordinal)
    ).all()
    edge_by_id = {edge.id: edge for edge in edges}
    beats = session.scalars(
        select(RelationshipBeat)
        .where(RelationshipBeat.graph_version_id == graph.id)
        .order_by(
            RelationshipBeat.episode_ordinal,
            RelationshipBeat.sequence,
            RelationshipBeat.ordinal,
        )
    ).all()
    return RelationshipGraphPayload.model_validate(
        {
            "schema_version": graph.schema_version,
            "edges": [
                {
                    "relationship_key": edge.relationship_key,
                    "source_character_key": edge.source_character_key,
                    "target_character_key": edge.target_character_key,
                    "directionality": edge.directionality,
                    "relationship_types": json.loads(edge.relationship_types_json),
                    "family_kinship": json.loads(edge.family_kinship_json) or None,
                    "surface_relationship": edge.surface_relationship,
                    "true_relationship": edge.true_relationship,
                    "source_view": json.loads(edge.source_view_json),
                    "target_view": json.loads(edge.target_view_json),
                    "trust_level": edge.trust_level,
                    "emotional_temperature": edge.emotional_temperature,
                    "power_balance": edge.power_balance,
                    "conflict_intensity": edge.conflict_intensity,
                    "story_function": edge.story_function,
                    "secret": edge.secret,
                    "is_core": edge.is_core,
                    "locked": edge.locked,
                    "ordinal": edge.ordinal,
                }
                for edge in edges
            ],
            "beats": [
                {
                    "relationship_key": edge_by_id[beat.relationship_edge_id].relationship_key,
                    "episode_ordinal": beat.episode_ordinal,
                    "sequence": beat.sequence,
                    "scene_ordinal": beat.scene_ordinal,
                    "trigger_type": beat.trigger_type,
                    "trigger_ref": beat.trigger_ref,
                    "before_state": json.loads(beat.before_state_json),
                    "after_state": json.loads(beat.after_state_json),
                    "evidence": beat.evidence,
                    "emotional_consequence": beat.emotional_consequence,
                    "audience_visibility": beat.audience_visibility,
                    "ordinal": beat.ordinal,
                }
                for beat in beats
            ],
            "core_relationship_keys": [edge.relationship_key for edge in edges if edge.is_core],
            "generation_notes": json.loads(graph.critic_json).get("generation_notes", []),
        }
    )


def relationship_graph_script_context(
    session: Session, graph: RelationshipGraphVersion
) -> dict[str, object]:
    payload = _graph_payload(session, graph).model_dump(mode="json")
    beat_rows = session.scalars(
        select(RelationshipBeat)
        .where(RelationshipBeat.graph_version_id == graph.id)
        .order_by(
            RelationshipBeat.episode_ordinal,
            RelationshipBeat.sequence,
            RelationshipBeat.ordinal,
        )
    ).all()
    for beat_payload, beat in zip(payload["beats"], beat_rows, strict=True):
        beat_payload["relationship_beat_id"] = beat.id
    return {
        "graph_version_id": graph.id,
        "content_hash": graph.content_hash,
        **payload,
    }


def replace_graph_payload(
    session: Session,
    graph: RelationshipGraphVersion,
    payload: RelationshipGraphPayload,
) -> None:
    session.execute(delete(RelationshipBeat).where(RelationshipBeat.graph_version_id == graph.id))
    session.execute(delete(RelationshipEdge).where(RelationshipEdge.graph_version_id == graph.id))
    session.flush()
    edge_ids: dict[str, str] = {}
    for edge_payload in payload.edges:
        edge_id = str(uuid4())
        edge_ids[edge_payload.relationship_key] = edge_id
        session.add(
            RelationshipEdge(
                id=edge_id,
                graph_version_id=graph.id,
                relationship_key=edge_payload.relationship_key,
                character_pair_key=canonical_character_pair_key(
                    edge_payload.source_character_key, edge_payload.target_character_key
                ),
                source_character_key=edge_payload.source_character_key,
                target_character_key=edge_payload.target_character_key,
                directionality=edge_payload.directionality,
                relationship_types_json=canonical_json(edge_payload.relationship_types),
                family_kinship_json=canonical_json(
                    edge_payload.family_kinship.model_dump(mode="json")
                    if edge_payload.family_kinship is not None
                    else {}
                ),
                surface_relationship=edge_payload.surface_relationship,
                true_relationship=edge_payload.true_relationship,
                source_view_json=canonical_json(edge_payload.source_view.model_dump(mode="json")),
                target_view_json=canonical_json(edge_payload.target_view.model_dump(mode="json")),
                trust_level=edge_payload.trust_level,
                emotional_temperature=edge_payload.emotional_temperature,
                power_balance=edge_payload.power_balance,
                conflict_intensity=edge_payload.conflict_intensity,
                story_function=edge_payload.story_function,
                secret=edge_payload.secret,
                is_core=edge_payload.is_core,
                locked=edge_payload.locked,
                ordinal=edge_payload.ordinal,
            )
        )
    session.flush()
    for beat_payload in payload.beats:
        session.add(
            RelationshipBeat(
                id=str(uuid4()),
                graph_version_id=graph.id,
                relationship_edge_id=edge_ids[beat_payload.relationship_key],
                episode_ordinal=beat_payload.episode_ordinal,
                sequence=beat_payload.sequence,
                scene_ordinal=beat_payload.scene_ordinal,
                trigger_type=beat_payload.trigger_type,
                trigger_ref=beat_payload.trigger_ref,
                before_state_json=canonical_json(beat_payload.before_state.model_dump(mode="json")),
                after_state_json=canonical_json(beat_payload.after_state.model_dump(mode="json")),
                evidence=beat_payload.evidence,
                emotional_consequence=beat_payload.emotional_consequence,
                audience_visibility=beat_payload.audience_visibility,
                ordinal=beat_payload.ordinal,
            )
        )
    graph.content_hash = content_hash(payload.model_dump(mode="json"))


def _validation_issues(
    payload: RelationshipGraphPayload, bible: StoryBibleVersion
) -> list[RelationshipGraphValidationIssue]:
    return validate_relationship_graph(payload, _story_bible_payload(bible))


def _issues_json(issues: list[RelationshipGraphValidationIssue]) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in issues]


def _reject_invalid_character_references(
    issues: list[RelationshipGraphValidationIssue],
) -> None:
    invalid = [item for item in issues if item.code == "INVALID_CHARACTER_REFERENCE"]
    if invalid:
        raise _http_error(
            422,
            "INVALID_CHARACTER_REFERENCE",
            "关系网引用了不存在的角色，未保存本次修改。",
            user_action="从当前角色设定中重新选择关系双方",
            details={"issues": _issues_json(invalid)},
        )


def graph_to_read(
    session: Session,
    graph: RelationshipGraphVersion,
    *,
    project: Project | None = None,
) -> dict[str, object]:
    project = project or project_or_404(session, graph.project_id)
    payload = _graph_payload(session, graph)
    critic = json.loads(graph.critic_json)
    return {
        "id": graph.id,
        "project_id": graph.project_id,
        "story_bible_version_id": graph.story_bible_version_id,
        "version": graph.version,
        "parent_version_id": graph.parent_version_id,
        "status": graph.status,
        "schema_version": graph.schema_version,
        "config_version": graph.config_version,
        "provider": graph.provider,
        "model": graph.model,
        "content_hash": graph.content_hash,
        "lock_version": graph.lock_version,
        "approved_at": graph.approved_at,
        "approved_by": graph.approved_by,
        "created_at": graph.created_at,
        "graph": payload.model_dump(mode="json"),
        "validation_issues": critic.get("validation_issues", []),
        "editability": graph_editability(session, project=project, graph=graph).model_dump(
            mode="json"
        ),
        "project_lock_version": project.lock_version,
    }


def list_relationship_graphs(session: Session, project_id: str) -> list[dict[str, object]]:
    project = project_or_404(session, project_id)
    graphs = session.scalars(
        select(RelationshipGraphVersion)
        .where(RelationshipGraphVersion.project_id == project_id)
        .order_by(RelationshipGraphVersion.version.desc())
    ).all()
    return [graph_to_read(session, graph, project=project) for graph in graphs]


def get_relationship_graph(session: Session, graph_id: str) -> dict[str, object]:
    return graph_to_read(session, _graph_or_404(session, graph_id))


def _check_project_version(project: Project, expected_version: int) -> None:
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)


def _check_graph_version(graph: RelationshipGraphVersion, expected_version: int) -> None:
    if graph.lock_version != expected_version:
        raise _http_error(
            409,
            "RELATIONSHIP_VERSION_CONFLICT",
            "关系版本已被其他修改更新。",
            user_action="刷新关系网并比较本地修改后重试",
            details={
                "graph_id": graph.id,
                "expected_version": expected_version,
                "latest_version": graph.lock_version,
            },
        )


def _require_semantic_editable(
    session: Session, project: Project, graph: RelationshipGraphVersion
) -> None:
    editability = graph_editability(session, project=project, graph=graph)
    if not editability.semantic_editable:
        raise _http_error(
            409,
            editability.reason_code or "RELATIONSHIP_GRAPH_NOT_EDITABLE",
            editability.reason_message or "当前关系版本不可编辑。",
            user_action="查看锁定原因或创建修改版",
            details={"graph_id": graph.id, "graph_status": graph.status},
        )


def _require_current_bible(session: Session, graph: RelationshipGraphVersion) -> StoryBibleVersion:
    bible = session.get(StoryBibleVersion, graph.story_bible_version_id)
    if bible is None:
        raise _http_error(409, "STORY_BIBLE_OUTDATED", "来源角色设定不存在。")
    latest_bible_id = session.scalar(
        select(StoryBibleVersion.id)
        .where(StoryBibleVersion.project_id == graph.project_id)
        .order_by(StoryBibleVersion.version.desc())
    )
    if latest_bible_id != bible.id:
        raise _http_error(
            409,
            "STORY_BIBLE_OUTDATED",
            "角色设定已变化，当前关系草稿不能作为最新基线。",
            user_action="基于最新角色设定创建关系草稿",
            details={
                "graph_story_bible_version_id": bible.id,
                "latest_story_bible_version_id": latest_bible_id,
            },
        )
    return bible


def create_relationship_graph(
    session: Session,
    *,
    project_id: str,
    expected_project_version: int,
    story_bible_version_id: str,
    payload: RelationshipGraphPayload,
    actor: str,
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    _check_project_version(project, expected_project_version)
    if project.status != EDITABLE_PROJECT_STATUS:
        raise _http_error(
            409,
            "PROJECT_EDIT_WINDOW_CLOSED",
            "当前项目阶段不能创建关系草稿。",
            user_action="完成故事结构生成后再创建关系草稿",
            details={"project_status": project.status},
        )
    bible = session.get(StoryBibleVersion, story_bible_version_id)
    if bible is None or bible.project_id != project_id:
        raise _http_error(409, "STORY_BIBLE_OUTDATED", "指定的角色设定不属于当前项目。")
    latest_bible_id = session.scalar(
        select(StoryBibleVersion.id)
        .where(StoryBibleVersion.project_id == project_id)
        .order_by(StoryBibleVersion.version.desc())
    )
    if latest_bible_id != bible.id:
        raise _http_error(409, "STORY_BIBLE_OUTDATED", "必须基于最新角色设定创建关系草稿。")
    version = (
        session.scalar(
            select(func.max(RelationshipGraphVersion.version)).where(
                RelationshipGraphVersion.project_id == project_id
            )
        )
        or 0
    ) + 1
    now = datetime.now(UTC)
    issues = _validation_issues(payload, bible)
    _reject_invalid_character_references(issues)
    graph = RelationshipGraphVersion(
        id=str(uuid4()),
        project_id=project_id,
        story_bible_version_id=bible.id,
        version=version,
        parent_version_id=None,
        status="DRAFT",
        schema_version=payload.schema_version,
        config_version="relationship-graph-v1",
        provider="manual",
        model="manual",
        critic_json=canonical_json(
            {
                "generation_notes": payload.generation_notes,
                "validation_issues": _issues_json(issues),
            }
        ),
        content_hash=content_hash(payload.model_dump(mode="json")),
        lock_version=1,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(graph)
    session.flush()
    replace_graph_payload(session, graph, payload)
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project_id,
        event_type="relationship_graph.created",
        payload={"graph_id": graph.id, "version": graph.version, "actor": actor},
    )
    session.commit()
    return graph_to_read(session, graph, project=project)


def _assert_locked_relationships_unchanged(
    current: RelationshipGraphPayload, updated: RelationshipGraphPayload
) -> None:
    current_edges = {edge.relationship_key: edge for edge in current.edges}
    updated_edges = {edge.relationship_key: edge for edge in updated.edges}
    current_beats = {
        edge.relationship_key: [
            beat.model_dump(mode="json")
            for beat in current.beats
            if beat.relationship_key == edge.relationship_key
        ]
        for edge in current.edges
    }
    updated_beats = {
        edge.relationship_key: [
            beat.model_dump(mode="json")
            for beat in updated.beats
            if beat.relationship_key == edge.relationship_key
        ]
        for edge in updated.edges
    }
    for relationship_key, edge in current_edges.items():
        updated_edge = updated_edges.get(relationship_key)
        if updated_edge is None:
            if edge.locked:
                raise _http_error(
                    409,
                    "RELATIONSHIP_LOCKED",
                    f"核心关系 {relationship_key} 已锁定，不能删除。",
                    user_action="先显式解除关系锁定",
                )
            continue
        if edge.locked and (
            edge.model_dump(mode="json") != updated_edge.model_dump(mode="json")
            or current_beats[relationship_key] != updated_beats[relationship_key]
        ):
            raise _http_error(
                409,
                "RELATIONSHIP_LOCKED",
                f"核心关系 {relationship_key} 已锁定，不能直接修改。",
                user_action="先显式解除关系锁定并查看影响",
            )
        if edge.locked != updated_edge.locked:
            raise _http_error(
                409,
                "RELATIONSHIP_LOCKED",
                "关系锁定状态只能通过明确的锁定或解锁操作修改。",
                user_action="使用关系锁定操作后重试保存",
            )


def update_relationship_graph(
    session: Session,
    *,
    graph_id: str,
    expected_project_version: int,
    expected_graph_version: int,
    payload: RelationshipGraphPayload,
    actor: str,
) -> dict[str, object]:
    graph = _graph_or_404(session, graph_id)
    project = project_or_404(session, graph.project_id)
    _check_project_version(project, expected_project_version)
    _check_graph_version(graph, expected_graph_version)
    _require_semantic_editable(session, project, graph)
    bible = _require_current_bible(session, graph)
    _assert_locked_relationships_unchanged(_graph_payload(session, graph), payload)
    issues = _validation_issues(payload, bible)
    _reject_invalid_character_references(issues)
    replace_graph_payload(session, graph, payload)
    graph.critic_json = canonical_json(
        {
            "generation_notes": payload.generation_notes,
            "validation_issues": _issues_json(issues),
        }
    )
    graph.lock_version += 1
    now = datetime.now(UTC)
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project.id,
        event_type="relationship_graph.updated",
        payload={"graph_id": graph.id, "actor": actor, "lock_version": graph.lock_version},
    )
    session.commit()
    return graph_to_read(session, graph, project=project)


def relationship_graph_validation(session: Session, graph_id: str) -> dict[str, object]:
    graph = _graph_or_404(session, graph_id)
    bible = session.get(StoryBibleVersion, graph.story_bible_version_id)
    if bible is None:
        raise _http_error(409, "STORY_BIBLE_OUTDATED", "来源角色设定不存在。")
    issues = _validation_issues(_graph_payload(session, graph), bible)
    return {
        "graph_id": graph.id,
        "valid_for_approval": not relationship_graph_has_blockers(issues),
        "issues": _issues_json(issues),
    }


def _transition_action(
    session: Session,
    *,
    graph_id: str,
    expected_project_version: int,
    expected_graph_version: int,
) -> tuple[RelationshipGraphVersion, Project]:
    graph = _graph_or_404(session, graph_id)
    project = project_or_404(session, graph.project_id)
    _check_project_version(project, expected_project_version)
    _check_graph_version(graph, expected_graph_version)
    if _active_job_type(session, graph.id) is not None:
        raise _http_error(409, "ACTIVE_RELATIONSHIP_JOB", "关系任务正在执行。")
    if project.status != EDITABLE_PROJECT_STATUS and not (
        graph.parent_version_id is not None and project.status in REVISION_PROJECT_STATUSES
    ):
        raise _http_error(
            409,
            "PROJECT_EDIT_WINDOW_CLOSED",
            "当前项目阶段不允许执行关系审核操作。",
            details={"project_status": project.status},
        )
    return graph, project


def submit_relationship_graph(
    session: Session,
    *,
    graph_id: str,
    expected_project_version: int,
    expected_graph_version: int,
    actor: str,
    note: str | None,
) -> dict[str, object]:
    graph, project = _transition_action(
        session,
        graph_id=graph_id,
        expected_project_version=expected_project_version,
        expected_graph_version=expected_graph_version,
    )
    if graph.status != "DRAFT":
        raise _http_error(409, "RELATIONSHIP_GRAPH_NOT_EDITABLE", "只有草稿可以提交审核。")
    bible = _require_current_bible(session, graph)
    issues = _validation_issues(_graph_payload(session, graph), bible)
    graph.critic_json = canonical_json(
        {
            "generation_notes": _graph_payload(session, graph).generation_notes,
            "validation_issues": _issues_json(issues),
        }
    )
    now = datetime.now(UTC)
    graph.status = "READY_FOR_REVIEW"
    graph.lock_version += 1
    project.lock_version += 1
    project.updated_at = now
    session.add(
        ReviewRecord(
            id=str(uuid4()),
            project_id=project.id,
            entity_type="relationship_graph",
            entity_id=graph.id,
            gate_key="RELATIONSHIP_GRAPH",
            risk_level="MEDIUM",
            status="PENDING_REVIEW",
            decision=None,
            issues_json=canonical_json(_issues_json(issues)),
            note=note,
            actor=actor,
            decided_at=None,
            created_at=now,
        )
    )
    append_event(
        session,
        project_id=project.id,
        event_type="relationship_graph.submitted",
        payload={"graph_id": graph.id, "actor": actor},
    )
    session.commit()
    return graph_to_read(session, graph, project=project)


def withdraw_relationship_graph(
    session: Session,
    *,
    graph_id: str,
    expected_project_version: int,
    expected_graph_version: int,
    actor: str,
    note: str | None,
) -> dict[str, object]:
    graph, project = _transition_action(
        session,
        graph_id=graph_id,
        expected_project_version=expected_project_version,
        expected_graph_version=expected_graph_version,
    )
    if graph.status != "READY_FOR_REVIEW":
        raise _http_error(409, "RELATIONSHIP_GRAPH_NOT_EDITABLE", "只有待审核版本可以撤回。")
    reviews = session.scalars(
        select(ReviewRecord).where(
            ReviewRecord.entity_type == "relationship_graph",
            ReviewRecord.entity_id == graph.id,
        )
    ).all()
    if any(review.decision is not None for review in reviews):
        raise _http_error(
            409,
            "RELATIONSHIP_REVISION_REQUIRED",
            "该版本已经产生审核决定，只能创建新的修改版。",
            user_action="复制为新草稿后继续修改",
        )
    now = datetime.now(UTC)
    for review in reviews:
        if review.status == "PENDING_REVIEW":
            review.status = "WITHDRAWN"
            review.note = note or review.note
            review.actor = actor
    graph.status = "DRAFT"
    graph.lock_version += 1
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project.id,
        event_type="relationship_graph.withdrawn",
        payload={"graph_id": graph.id, "actor": actor},
    )
    session.commit()
    return graph_to_read(session, graph, project=project)


def _create_revision_copy(
    session: Session,
    *,
    source: RelationshipGraphVersion,
    actor: str,
    note: str | None,
) -> RelationshipGraphVersion:
    payload = _graph_payload(session, source)
    bible = session.get(StoryBibleVersion, source.story_bible_version_id)
    issues = _validation_issues(payload, bible) if bible is not None else []
    version = (
        session.scalar(
            select(func.max(RelationshipGraphVersion.version)).where(
                RelationshipGraphVersion.project_id == source.project_id
            )
        )
        or 0
    ) + 1
    now = datetime.now(UTC)
    revision = RelationshipGraphVersion(
        id=str(uuid4()),
        project_id=source.project_id,
        story_bible_version_id=source.story_bible_version_id,
        version=version,
        parent_version_id=source.id,
        status="DRAFT",
        schema_version=source.schema_version,
        config_version=source.config_version,
        provider="manual",
        model="manual-revision",
        critic_json=canonical_json(
            {
                "generation_notes": [*payload.generation_notes, *([note] if note else [])],
                "validation_issues": _issues_json(issues),
            }
        ),
        content_hash=content_hash(payload.model_dump(mode="json")),
        lock_version=1,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(revision)
    session.flush()
    replace_graph_payload(session, revision, payload)
    append_event(
        session,
        project_id=source.project_id,
        event_type="relationship_graph.revision_created",
        payload={"source_graph_id": source.id, "graph_id": revision.id, "actor": actor},
    )
    return revision


def reject_relationship_graph(
    session: Session,
    *,
    graph_id: str,
    expected_project_version: int,
    expected_graph_version: int,
    actor: str,
    note: str,
    issues: list[str],
) -> dict[str, object]:
    graph, project = _transition_action(
        session,
        graph_id=graph_id,
        expected_project_version=expected_project_version,
        expected_graph_version=expected_graph_version,
    )
    if graph.status != "READY_FOR_REVIEW":
        raise _http_error(409, "RELATIONSHIP_GRAPH_NOT_EDITABLE", "只有待审核版本可以退回修改。")
    now = datetime.now(UTC)
    pending = session.scalar(
        select(ReviewRecord)
        .where(
            ReviewRecord.entity_type == "relationship_graph",
            ReviewRecord.entity_id == graph.id,
            ReviewRecord.status == "PENDING_REVIEW",
        )
        .order_by(ReviewRecord.created_at.desc())
    )
    if pending is None:
        pending = ReviewRecord(
            id=str(uuid4()),
            project_id=project.id,
            entity_type="relationship_graph",
            entity_id=graph.id,
            gate_key="RELATIONSHIP_GRAPH",
            risk_level="MEDIUM",
            status="PENDING_REVIEW",
            decision=None,
            issues_json="[]",
            note=None,
            actor=None,
            decided_at=None,
            created_at=now,
        )
        session.add(pending)
    pending.status = "REJECTED"
    pending.decision = "REJECT"
    pending.issues_json = canonical_json(issues)
    pending.note = note
    pending.actor = actor
    pending.decided_at = now
    graph.status = "SUPERSEDED"
    graph.lock_version += 1
    revision = _create_revision_copy(session, source=graph, actor=actor, note=note)
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project.id,
        event_type="relationship_graph.rejected",
        payload={"graph_id": graph.id, "revision_graph_id": revision.id, "actor": actor},
    )
    session.commit()
    return {
        "rejected_graph": graph_to_read(session, graph, project=project),
        "revision_graph": graph_to_read(session, revision, project=project),
    }


def approve_relationship_graph(
    session: Session,
    *,
    graph_id: str,
    expected_project_version: int,
    expected_graph_version: int,
    actor: str,
    note: str | None,
    trace_id: str,
) -> dict[str, object]:
    graph, project = _transition_action(
        session,
        graph_id=graph_id,
        expected_project_version=expected_project_version,
        expected_graph_version=expected_graph_version,
    )
    if graph.status not in {"DRAFT", "READY_FOR_REVIEW"}:
        raise _http_error(409, "RELATIONSHIP_GRAPH_NOT_EDITABLE", "当前版本不能批准。")
    bible = _require_current_bible(session, graph)
    payload = _graph_payload(session, graph)
    issues = _validation_issues(payload, bible)
    if relationship_graph_has_blockers(issues):
        raise _http_error(
            422,
            "RELATIONSHIP_GRAPH_VALIDATION_FAILED",
            "关系网尚未达到批准条件。",
            user_action="查看检查结果并修正阻断项",
            details={"issues": _issues_json(issues)},
        )
    now = datetime.now(UTC)
    old_approved = session.scalars(
        select(RelationshipGraphVersion).where(
            RelationshipGraphVersion.project_id == project.id,
            RelationshipGraphVersion.status == "APPROVED",
            RelationshipGraphVersion.id != graph.id,
        )
    ).all()
    for old_graph in old_approved:
        old_graph.status = "SUPERSEDED"
        old_graph.lock_version += 1
    graph.status = "APPROVED"
    graph.lock_version += 1
    graph.approved_at = now
    graph.approved_by = actor
    graph.critic_json = canonical_json(
        {
            "generation_notes": payload.generation_notes,
            "validation_issues": _issues_json(issues),
        }
    )
    bible.status = "APPROVED"
    bible.approved_at = now
    bible.approved_by = actor
    pending = session.scalar(
        select(ReviewRecord)
        .where(
            ReviewRecord.entity_type == "relationship_graph",
            ReviewRecord.entity_id == graph.id,
            ReviewRecord.status == "PENDING_REVIEW",
        )
        .order_by(ReviewRecord.created_at.desc())
    )
    if pending is None:
        pending = ReviewRecord(
            id=str(uuid4()),
            project_id=project.id,
            entity_type="relationship_graph",
            entity_id=graph.id,
            gate_key="RELATIONSHIP_GRAPH",
            risk_level="MEDIUM",
            status="PENDING_REVIEW",
            decision=None,
            issues_json="[]",
            note=None,
            actor=None,
            decided_at=None,
            created_at=now,
        )
        session.add(pending)
    pending.status = "APPROVED"
    pending.decision = "APPROVE"
    pending.issues_json = canonical_json(_issues_json(issues))
    pending.note = note
    pending.actor = actor
    pending.decided_at = now
    from app.services.character_visuals import prepare_character_visuals

    characters = prepare_character_visuals(
        session,
        project=project,
        bible=bible,
        graph=graph,
    )
    project.status = "CHARACTER_VISUAL_READY"
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="relationship_graph.approved",
        payload={
            "graph_id": graph.id,
            "story_bible_version_id": bible.id,
            "actor": actor,
            "character_ids": [item.id for item in characters],
            "next_gate": "CHARACTER_IDENTITY_LOCK",
        },
    )
    session.commit()
    result = graph_to_read(session, graph, project=project)
    result["character_visuals"] = {
        "character_count": len(characters),
        "route": f"/projects/{project.id}/characters",
    }
    return result


def enqueue_script_package_for_locked_identities(
    session: Session,
    *,
    project: Project,
    actor: str,
    trace_id: str,
) -> tuple[Job, bool]:
    graph = session.scalar(
        select(RelationshipGraphVersion)
        .where(
            RelationshipGraphVersion.project_id == project.id,
            RelationshipGraphVersion.status == "APPROVED",
        )
        .order_by(RelationshipGraphVersion.version.desc())
    )
    if graph is None:
        raise ValueError("已批准角色关系网不存在")
    bible = session.get(StoryBibleVersion, graph.story_bible_version_id)
    if bible is None or bible.status != "APPROVED":
        raise ValueError("已批准角色设定不存在")
    business_key = (
        f"{project.id}:GENERATE_SCRIPT_PACKAGE:{graph.id}:"
        f"{graph.content_hash}:{SCRIPT_PACKAGE_CONFIG_VERSION}"
    )
    return enqueue_job(
        session,
        project_id=project.id,
        job_type="GENERATE_SCRIPT_PACKAGE",
        entity_type="relationship_graph",
        entity_id=graph.id,
        idempotency_key=business_key,
        input_payload={
            "project_id": project.id,
            "story_bible_version_id": bible.id,
            "relationship_graph_id": graph.id,
            "relationship_graph_content_hash": graph.content_hash,
            "actor": actor,
            "config_version": SCRIPT_PACKAGE_CONFIG_VERSION,
        },
        label=f"{project.name} · 关系与锁定身份驱动剧本包",
        stage="等待基于批准关系网与锁定角色身份生成分集大纲和剧本",
        trace_id=trace_id,
        estimated_seconds=180,
        retryable=True,
    )


def _diff_priority(fields: set[str], *, core: bool = False) -> str:
    if fields & {"source_character_key", "target_character_key", "locked"}:
        return "P0"
    if core or fields & {
        "true_relationship",
        "story_function",
        "secret",
        "is_core",
        "family_kinship",
    }:
        return "P1"
    if fields & {
        "relationship_types",
        "directionality",
        "source_view",
        "target_view",
        "trust_level",
        "emotional_temperature",
        "power_balance",
        "conflict_intensity",
        "surface_relationship",
    }:
        return "P2"
    if fields & {"ordinal"}:
        return "P4"
    return "P3"


def relationship_graph_diff(session: Session, from_id: str, to_id: str) -> dict[str, object]:
    source = _graph_or_404(session, from_id)
    target = _graph_or_404(session, to_id)
    if source.project_id != target.project_id:
        raise _http_error(
            422,
            "RELATIONSHIP_DIFF_PROJECT_MISMATCH",
            "只能比较同一项目内的关系版本。",
        )
    left = _graph_payload(session, source).model_dump(mode="json")
    right = _graph_payload(session, target).model_dump(mode="json")
    changes: list[dict[str, object]] = []
    left_edges = {item["relationship_key"]: item for item in left["edges"]}
    right_edges = {item["relationship_key"]: item for item in right["edges"]}
    for key in sorted(left_edges.keys() | right_edges.keys()):
        before = left_edges.get(key)
        after = right_edges.get(key)
        if before is None and after is not None:
            priority = "P1" if after["is_core"] else "P2"
            changes.append(
                {
                    "category": "RELATIONSHIP_ADDED",
                    "priority": priority,
                    "relationship_key": key,
                    "fields": [],
                    "before": None,
                    "after": after,
                    "summary": f"新增{'核心' if after['is_core'] else ''}关系 {key}",
                }
            )
            continue
        if after is None and before is not None:
            priority = "P1" if before["is_core"] else "P2"
            changes.append(
                {
                    "category": "RELATIONSHIP_REMOVED",
                    "priority": priority,
                    "relationship_key": key,
                    "fields": [],
                    "before": before,
                    "after": None,
                    "summary": f"删除{'核心' if before['is_core'] else ''}关系 {key}",
                }
            )
            continue
        assert before is not None and after is not None
        fields = {field for field in before if before[field] != after[field]}
        if fields:
            priority = _diff_priority(fields, core=bool(before["is_core"] or after["is_core"]))
            changes.append(
                {
                    "category": "RELATIONSHIP_CHANGED",
                    "priority": priority,
                    "relationship_key": key,
                    "fields": sorted(fields),
                    "before": {field: before[field] for field in sorted(fields)},
                    "after": {field: after[field] for field in sorted(fields)},
                    "summary": f"关系 {key} 修改了 {len(fields)} 个字段",
                }
            )

    def beat_key(item: dict[str, object]) -> tuple[object, ...]:
        return (item["relationship_key"], item["episode_ordinal"], item["ordinal"])

    left_beats = {beat_key(item): item for item in left["beats"]}
    right_beats = {beat_key(item): item for item in right["beats"]}
    for key in sorted(left_beats.keys() | right_beats.keys()):
        before = left_beats.get(key)
        after = right_beats.get(key)
        relationship_key, episode_ordinal, ordinal = key
        if before is None:
            category, fields, summary = "BEAT_ADDED", [], "新增关系变化事件"
        elif after is None:
            category, fields, summary = "BEAT_REMOVED", [], "删除关系变化事件"
        else:
            fields = sorted(field for field in before if before[field] != after[field])
            if not fields:
                continue
            category, summary = "BEAT_CHANGED", f"关系变化事件修改了 {len(fields)} 个字段"
        changes.append(
            {
                "category": category,
                "priority": "P2",
                "relationship_key": relationship_key,
                "episode_ordinal": episode_ordinal,
                "beat_ordinal": ordinal,
                "fields": fields,
                "before": before,
                "after": after,
                "summary": summary,
            }
        )
    priority_order = {f"P{number}": number for number in range(5)}
    counts = {
        priority: sum(item["priority"] == priority for item in changes)
        for priority in priority_order
    }
    highest = min((item["priority"] for item in changes), key=priority_order.get, default=None)
    return {
        "from_graph_id": source.id,
        "to_graph_id": target.id,
        "from_version": source.version,
        "to_version": target.version,
        "highest_priority": highest,
        "counts": counts,
        "changes": changes,
    }


def analyze_relationship_revision(
    session: Session,
    *,
    project_id: str,
    base_relationship_graph_id: str,
    relationship_keys: list[str],
    intent: str,
    expected_version: int,
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    _check_project_version(project, expected_version)
    source = _graph_or_404(session, base_relationship_graph_id)
    if source.project_id != project.id or source.status not in {"APPROVED", "SUPERSEDED"}:
        raise _http_error(
            409,
            "RELATIONSHIP_REVISION_REQUIRED",
            "只能从已批准的关系版本创建修改版。",
        )
    payload = _graph_payload(session, source)
    known_keys = {edge.relationship_key for edge in payload.edges}
    missing = sorted(set(relationship_keys) - known_keys)
    if missing:
        raise _http_error(
            422,
            "RELATIONSHIP_NOT_FOUND",
            "影响分析包含不存在的关系。",
            details={"relationship_keys": missing},
        )
    selected_keys = sorted(set(relationship_keys))
    beat_targets = [beat for beat in payload.beats if beat.relationship_key in selected_keys]
    episode_ordinals = sorted({beat.episode_ordinal for beat in beat_targets})
    outlines = session.scalars(
        select(EpisodeOutlineVersion).where(
            EpisodeOutlineVersion.project_id == project.id,
            EpisodeOutlineVersion.relationship_graph_version_id == source.id,
        )
    ).all()
    scripts = session.scalars(
        select(ScriptVersion).where(
            ScriptVersion.project_id == project.id,
            ScriptVersion.relationship_graph_version_id == source.id,
        )
    ).all()
    if not episode_ordinals:
        episode_ordinals = sorted({item.episode_ordinal for item in [*outlines, *scripts]})
    affected_scripts = [item for item in scripts if item.episode_ordinal in episode_ordinals]
    scene_rows: list[ScriptScene] = []
    for script in affected_scripts:
        candidates = session.scalars(
            select(ScriptScene)
            .where(ScriptScene.script_version_id == script.id)
            .order_by(ScriptScene.ordinal)
        ).all()
        target_ordinals = {
            beat.scene_ordinal
            for beat in beat_targets
            if beat.episode_ordinal == script.episode_ordinal and beat.scene_ordinal is not None
        }
        scene_rows.extend(
            [scene for scene in candidates if scene.ordinal in target_ordinals]
            if target_ordinals
            else candidates
        )
    touches_approved = any(item.status == "APPROVED" for item in [*outlines, *scripts])
    affected = {
        "episode_ordinals": episode_ordinals,
        "outline_version_ids": [
            item.id for item in outlines if item.episode_ordinal in episode_ordinals
        ],
        "script_version_ids": [item.id for item in affected_scripts],
        "scenes": [
            {"id": scene.id, "ordinal": scene.ordinal, "heading": scene.heading}
            for scene in scene_rows
        ],
        "regenerate_asset_types": ["分集大纲", "剧本", "分镜", "临时声音", "动态预演"],
        "preserved_asset_types": ["故事设定", "角色视觉定稿", "未受影响集数"],
    }
    estimate = {
        "points": 20 * max(1, len(episode_ordinals)),
        "seconds": 45 * max(1, len(episode_ordinals)),
    }
    signature_payload = {
        "project_id": project.id,
        "project_version": project.lock_version,
        "base_relationship_graph_id": source.id,
        "base_content_hash": source.content_hash,
        "relationship_keys": selected_keys,
        "intent": intent,
        "affected": affected,
        "estimate": estimate,
        "touches_approved": touches_approved,
    }
    return {
        **signature_payload,
        "impact_hash": content_hash(signature_payload),
        "requires_confirmation": True,
    }


def create_confirmed_relationship_revision(
    session: Session,
    *,
    project_id: str,
    base_relationship_graph_id: str,
    relationship_keys: list[str],
    intent: str,
    expected_version: int,
    confirmed: bool,
    impact_hash: str,
    actor: str,
) -> dict[str, object]:
    if not confirmed:
        raise _http_error(
            409,
            "RELATIONSHIP_REVISION_CONFIRMATION_REQUIRED",
            "创建关系修改版前必须确认下游影响。",
        )
    impact = analyze_relationship_revision(
        session,
        project_id=project_id,
        base_relationship_graph_id=base_relationship_graph_id,
        relationship_keys=relationship_keys,
        intent=intent,
        expected_version=expected_version,
    )
    if impact_hash != impact["impact_hash"]:
        raise _http_error(
            409,
            "RELATIONSHIP_REVISION_IMPACT_STALE",
            "影响范围已经变化，请重新查看并确认。",
            user_action="重新执行关系修订影响分析",
        )
    source = _graph_or_404(session, base_relationship_graph_id)
    open_revision = session.scalar(
        select(RelationshipGraphVersion.id).where(
            RelationshipGraphVersion.project_id == project_id,
            RelationshipGraphVersion.status.in_({"DRAFT", "READY_FOR_REVIEW"}),
        )
    )
    if open_revision is not None:
        raise _http_error(
            409,
            "RELATIONSHIP_REVISION_ALREADY_OPEN",
            "项目已有未完成的关系修改版，不能重复创建。",
            user_action="继续编辑或审核现有修改版",
            details={"relationship_graph_id": open_revision},
        )
    if _active_job_type(session, source.id) is not None:
        raise _http_error(409, "ACTIVE_RELATIONSHIP_JOB", "关系任务正在执行，暂时不能创建修改版。")
    revision = _create_revision_copy(session, source=source, actor=actor, note=intent)
    now = datetime.now(UTC)
    change_set = ChangeSet(
        id=str(uuid4()),
        project_id=project_id,
        base_timeline_id=None,
        base_relationship_graph_id=source.id,
        scope_json=canonical_json({"type": "RELATIONSHIP", "ids": sorted(set(relationship_keys))}),
        instruction=intent,
        impact_json=canonical_json(impact),
        estimate_json=canonical_json(impact["estimate"]),
        status="CONFIRMED",
        result_timeline_id=None,
        result_relationship_graph_id=revision.id,
        created_at=now,
    )
    session.add(change_set)
    project = project_or_404(session, project_id)
    project.lock_version += 1
    project.preview_approved = False
    project.export_ready = False
    project.updated_at = now
    append_event(
        session,
        project_id=project.id,
        event_type="relationship_graph.revision_impact_confirmed",
        payload={
            "change_set_id": change_set.id,
            "source_graph_id": source.id,
            "revision_graph_id": revision.id,
            "impact_hash": impact_hash,
        },
    )
    session.commit()
    return {
        "revision_graph": graph_to_read(session, revision, project=project),
        "change_set": {
            "id": change_set.id,
            "status": change_set.status,
            "base_relationship_graph_id": source.id,
            "result_relationship_graph_id": revision.id,
            "impact": impact,
            "created_at": change_set.created_at,
        },
    }


def create_relationship_graph_revision(
    session: Session,
    *,
    graph_id: str,
    expected_project_version: int,
    actor: str,
    note: str | None,
) -> dict[str, object]:
    source = _graph_or_404(session, graph_id)
    project = project_or_404(session, source.project_id)
    _check_project_version(project, expected_project_version)
    if project.status != EDITABLE_PROJECT_STATUS:
        raise _http_error(
            409,
            "DOWNSTREAM_IMPACT_CONFIRMATION_REQUIRED",
            "当前项目已有下游内容，创建关系修改版前必须确认影响范围。",
            user_action="先执行关系修订影响分析",
            details={"project_status": project.status},
        )
    if source.status not in {"APPROVED", "SUPERSEDED"}:
        raise _http_error(409, "RELATIONSHIP_REVISION_REQUIRED", "当前版本不能创建修改版。")
    _require_current_bible(session, source)
    revision = _create_revision_copy(session, source=source, actor=actor, note=note)
    now = datetime.now(UTC)
    project.lock_version += 1
    project.updated_at = now
    session.commit()
    return graph_to_read(session, revision, project=project)


def set_relationship_lock(
    session: Session,
    *,
    graph_id: str,
    relationship_key: str,
    expected_project_version: int,
    expected_graph_version: int,
    actor: str,
    locked: bool,
) -> dict[str, object]:
    graph = _graph_or_404(session, graph_id)
    project = project_or_404(session, graph.project_id)
    _check_project_version(project, expected_project_version)
    _check_graph_version(graph, expected_graph_version)
    _require_semantic_editable(session, project, graph)
    edge = session.scalar(
        select(RelationshipEdge).where(
            RelationshipEdge.graph_version_id == graph.id,
            RelationshipEdge.relationship_key == relationship_key,
        )
    )
    if edge is None:
        raise _http_error(
            404,
            "RELATIONSHIP_NOT_FOUND",
            "指定的角色关系不存在。",
            details={"relationship_key": relationship_key},
        )
    if edge.locked == locked:
        return graph_to_read(session, graph, project=project)
    edge.locked = locked
    session.flush()
    payload = _graph_payload(session, graph)
    graph.content_hash = content_hash(payload.model_dump(mode="json"))
    graph.lock_version += 1
    now = datetime.now(UTC)
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project.id,
        event_type="relationship.locked" if locked else "relationship.unlocked",
        payload={"graph_id": graph.id, "relationship_key": relationship_key, "actor": actor},
    )
    session.commit()
    return graph_to_read(session, graph, project=project)
