from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import (
    RelationshipGraphActionRequest,
    RelationshipGraphCreateRequest,
    RelationshipGraphRejectRequest,
    RelationshipGraphRevisionRequest,
    RelationshipGraphUpdateRequest,
    RelationshipRevisionCreateRequest,
    RelationshipRevisionImpactRequest,
)
from app.services.relationship_graph_workflow import (
    analyze_relationship_revision,
    approve_relationship_graph,
    create_confirmed_relationship_revision,
    create_relationship_graph,
    create_relationship_graph_revision,
    get_relationship_graph,
    list_relationship_graphs,
    reject_relationship_graph,
    relationship_graph_diff,
    relationship_graph_validation,
    set_relationship_lock,
    submit_relationship_graph,
    update_relationship_graph,
    withdraw_relationship_graph,
)

router = APIRouter(prefix="/api/v1", tags=["relationship-graphs"])


@router.get("/projects/{project_id}/relationship-graphs")
def project_relationship_graphs(
    project_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(list_relationship_graphs(session, project_id))


@router.post(
    "/projects/{project_id}/relationship-graphs",
    status_code=status.HTTP_201_CREATED,
)
def create_project_relationship_graph(
    project_id: str,
    payload: RelationshipGraphCreateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        create_relationship_graph(
            session,
            project_id=project_id,
            expected_project_version=payload.expected_project_version,
            story_bible_version_id=payload.story_bible_version_id,
            payload=payload.graph,
            actor=payload.actor,
        )
    )


@router.get("/relationship-graphs/{graph_id}")
def relationship_graph(graph_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(get_relationship_graph(session, graph_id))


@router.patch("/relationship-graphs/{graph_id}")
def edit_relationship_graph(
    graph_id: str,
    payload: RelationshipGraphUpdateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        update_relationship_graph(
            session,
            graph_id=graph_id,
            expected_project_version=payload.expected_project_version,
            expected_graph_version=payload.expected_graph_version,
            payload=payload.graph_payload(),
            actor=payload.actor,
        )
    )


@router.get("/relationship-graphs/{graph_id}/validation")
def validate_relationship_graph_version(
    graph_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(relationship_graph_validation(session, graph_id))


@router.get("/relationship-graphs/{from_id}/diff/{to_id}")
def compare_relationship_graph_versions(
    from_id: str, to_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(relationship_graph_diff(session, from_id, to_id))


@router.post("/projects/{project_id}/relationship-revision-impact")
def relationship_revision_impact(
    project_id: str,
    payload: RelationshipRevisionImpactRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        analyze_relationship_revision(
            session,
            project_id=project_id,
            base_relationship_graph_id=payload.base_relationship_graph_id,
            relationship_keys=payload.relationship_keys,
            intent=payload.intent,
            expected_version=payload.expected_version,
        )
    )


@router.post("/projects/{project_id}/relationship-revisions", status_code=status.HTTP_201_CREATED)
def create_project_relationship_revision(
    project_id: str,
    payload: RelationshipRevisionCreateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        create_confirmed_relationship_revision(
            session,
            project_id=project_id,
            base_relationship_graph_id=payload.base_relationship_graph_id,
            relationship_keys=payload.relationship_keys,
            intent=payload.intent,
            expected_version=payload.expected_version,
            confirmed=payload.confirmed,
            impact_hash=payload.impact_hash,
            actor=payload.actor,
        )
    )


@router.post("/relationship-graphs/{graph_id}/submit")
def submit_relationship_graph_version(
    graph_id: str,
    payload: RelationshipGraphActionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        submit_relationship_graph(
            session,
            graph_id=graph_id,
            expected_project_version=payload.expected_project_version,
            expected_graph_version=payload.expected_graph_version,
            actor=payload.actor,
            note=payload.note,
        )
    )


@router.post("/relationship-graphs/{graph_id}/withdraw")
def withdraw_relationship_graph_version(
    graph_id: str,
    payload: RelationshipGraphActionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        withdraw_relationship_graph(
            session,
            graph_id=graph_id,
            expected_project_version=payload.expected_project_version,
            expected_graph_version=payload.expected_graph_version,
            actor=payload.actor,
            note=payload.note,
        )
    )


@router.post("/relationship-graphs/{graph_id}/reject")
def reject_relationship_graph_version(
    graph_id: str,
    payload: RelationshipGraphRejectRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        reject_relationship_graph(
            session,
            graph_id=graph_id,
            expected_project_version=payload.expected_project_version,
            expected_graph_version=payload.expected_graph_version,
            actor=payload.actor,
            note=payload.note,
            issues=payload.issues,
        )
    )


@router.post("/relationship-graphs/{graph_id}/approve")
def approve_relationship_graph_version(
    graph_id: str,
    payload: RelationshipGraphActionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        approve_relationship_graph(
            session,
            graph_id=graph_id,
            expected_project_version=payload.expected_project_version,
            expected_graph_version=payload.expected_graph_version,
            actor=payload.actor,
            note=payload.note,
            trace_id=get_trace_id(),
        )
    )


@router.post("/relationship-graphs/{graph_id}/revisions", status_code=status.HTTP_201_CREATED)
def create_relationship_graph_revision_version(
    graph_id: str,
    payload: RelationshipGraphRevisionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        create_relationship_graph_revision(
            session,
            graph_id=graph_id,
            expected_project_version=payload.expected_project_version,
            actor=payload.actor,
            note=payload.note,
        )
    )


@router.post("/relationship-graphs/{graph_id}/relationships/{relationship_key}/lock")
def lock_relationship(
    graph_id: str,
    relationship_key: str,
    payload: RelationshipGraphActionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        set_relationship_lock(
            session,
            graph_id=graph_id,
            relationship_key=relationship_key,
            expected_project_version=payload.expected_project_version,
            expected_graph_version=payload.expected_graph_version,
            actor=payload.actor,
            locked=True,
        )
    )


@router.post("/relationship-graphs/{graph_id}/relationships/{relationship_key}/unlock")
def unlock_relationship(
    graph_id: str,
    relationship_key: str,
    payload: RelationshipGraphActionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        set_relationship_lock(
            session,
            graph_id=graph_id,
            relationship_key=relationship_key,
            expected_project_version=payload.expected_project_version,
            expected_graph_version=payload.expected_graph_version,
            actor=payload.actor,
            locked=False,
        )
    )
