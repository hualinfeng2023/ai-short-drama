from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.trace import success
from app.config import get_settings
from app.db.models import Project, RelationshipGraphVersion, StoryBibleVersion
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import (
    RelationshipGraphActionRequest,
    RelationshipGraphCreateRequest,
    RelationshipGraphRejectRequest,
    RelationshipGraphRevisionRequest,
    RelationshipGraphUpdateRequest,
    RelationshipRevisionCreateRequest,
    RelationshipRevisionImpactRequest,
    RelationshipUpbringingSuggestionRequest,
)
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash
from app.services.relationship_assistant import suggest_relationship_upbringing
from app.services.relationship_graph_workflow import (
    analyze_relationship_revision,
    get_relationship_graph,
    list_relationship_graphs,
    relationship_graph_diff,
    relationship_graph_validation,
)

router = APIRouter(prefix="/api/v1", tags=["relationship-graphs"])


def _dispatch_graph_command(
    session: Session,
    *,
    graph: RelationshipGraphVersion,
    command_type: str,
    expected_project_version: int,
    expected_graph_version: int | None,
    actor: str,
    payload: dict[str, object],
    idempotency_key: str | None,
    route_key: str,
) -> tuple[dict[str, object], bool]:
    request_fingerprint = content_hash(
        {
            "route": route_key,
            "graph_id": graph.id,
            "expected_project_version": expected_project_version,
            "expected_graph_version": expected_graph_version,
            "actor": actor,
            "payload": payload,
        }
    )
    effective_key = idempotency_key or f"relationship-command-{request_fingerprint}"
    execution = dispatch_domain_command(
        session,
        project_id=graph.project_id,
        command=DirectorCommand(
            command_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{graph.project_id}:{command_type}:{effective_key}",
                )
            ),
            command_type=command_type,
            actor=CommandActor(type="USER", id=actor),
            target_object_id=graph.id,
            target_version_id=graph.id,
            expected_version=ExpectedVersion(
                project_lock_version=expected_project_version,
                object_lock_version=expected_graph_version,
                target_version_id=graph.id,
                target_hash=graph.content_hash,
            ),
            payload={"confirmed": True, **payload},
            idempotency_key=effective_key,
        ),
        request_fingerprint=request_fingerprint,
    )
    return execution.result, execution.idempotency_replayed


def _graph_or_http_404(session: Session, graph_id: str) -> RelationshipGraphVersion:
    graph = session.get(RelationshipGraphVersion, graph_id)
    if graph is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "RELATIONSHIP_GRAPH_NOT_FOUND", "message": "角色关系网不存在"},
        )
    return graph


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
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = session.get(Project, project_id)
    bible = session.get(StoryBibleVersion, payload.story_bible_version_id)
    if project is None or bible is None or bible.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "STORY_BIBLE_OUTDATED", "message": "指定的角色设定不存在"},
        )
    command_payload = {
        "story_bible_version_id": bible.id,
        "graph": payload.graph.model_dump(mode="json"),
    }
    fingerprint = content_hash(
        {
            "route": f"relationship-graphs:create:{project.id}",
            "expected_project_version": payload.expected_project_version,
            "actor": payload.actor,
            "payload": command_payload,
        }
    )
    effective_key = idempotency_key or f"relationship-command-{fingerprint}"
    execution = dispatch_domain_command(
        session,
        project_id=project.id,
        command=DirectorCommand(
            command_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project.id}:CREATE_RELATIONSHIP_GRAPH:{effective_key}",
                )
            ),
            command_type="CREATE_RELATIONSHIP_GRAPH",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=project.id,
            target_version_id=bible.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_project_version,
                target_version_id=bible.id,
                target_hash=bible.content_hash,
            ),
            payload={**command_payload, "confirmed": True},
            idempotency_key=effective_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/relationship-graphs/{graph_id}")
def relationship_graph(graph_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(get_relationship_graph(session, graph_id))


@router.post(
    "/relationship-graphs/{graph_id}/relationships/{relationship_key}/upbringing-suggestion"
)
async def relationship_upbringing_suggestion(
    graph_id: str,
    relationship_key: str,
    payload: RelationshipUpbringingSuggestionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        await suggest_relationship_upbringing(
            session,
            graph_id=graph_id,
            relationship_key=relationship_key,
            payload=payload,
            settings=get_settings(),
        )
    )


@router.patch("/relationship-graphs/{graph_id}")
def edit_relationship_graph(
    graph_id: str,
    payload: RelationshipGraphUpdateRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_graph_command(
        session,
        graph=_graph_or_http_404(session, graph_id),
        command_type="UPDATE_RELATIONSHIP_GRAPH",
        expected_project_version=payload.expected_project_version,
        expected_graph_version=payload.expected_graph_version,
        actor=payload.actor,
        payload=payload.model_dump(
            mode="json",
            exclude={"expected_project_version", "expected_graph_version", "actor"},
        ),
        idempotency_key=idempotency_key,
        route_key=f"relationship-graphs:update:{graph_id}",
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


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
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    graph = _graph_or_http_404(session, payload.base_relationship_graph_id)
    if graph.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "RELATIONSHIP_GRAPH_NOT_FOUND", "message": "角色关系网不存在"},
        )
    result, replayed = _dispatch_graph_command(
        session,
        graph=graph,
        command_type="CREATE_CONFIRMED_RELATIONSHIP_REVISION",
        expected_project_version=payload.expected_version,
        expected_graph_version=None,
        actor=payload.actor,
        payload={
            "base_relationship_graph_id": graph.id,
            "relationship_keys": payload.relationship_keys,
            "intent": payload.intent,
            "confirmed": payload.confirmed,
            "impact_hash": payload.impact_hash,
        },
        idempotency_key=idempotency_key,
        route_key=f"relationship-revisions:confirmed:{project_id}:{graph.id}",
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/relationship-graphs/{graph_id}/submit")
def submit_relationship_graph_version(
    graph_id: str,
    payload: RelationshipGraphActionRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_graph_command(
        session,
        graph=_graph_or_http_404(session, graph_id),
        command_type="SUBMIT_RELATIONSHIP_GRAPH",
        expected_project_version=payload.expected_project_version,
        expected_graph_version=payload.expected_graph_version,
        actor=payload.actor,
        payload={"note": payload.note},
        idempotency_key=idempotency_key,
        route_key=f"relationship-graphs:submit:{graph_id}",
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/relationship-graphs/{graph_id}/withdraw")
def withdraw_relationship_graph_version(
    graph_id: str,
    payload: RelationshipGraphActionRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_graph_command(
        session,
        graph=_graph_or_http_404(session, graph_id),
        command_type="WITHDRAW_RELATIONSHIP_GRAPH",
        expected_project_version=payload.expected_project_version,
        expected_graph_version=payload.expected_graph_version,
        actor=payload.actor,
        payload={"note": payload.note},
        idempotency_key=idempotency_key,
        route_key=f"relationship-graphs:withdraw:{graph_id}",
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/relationship-graphs/{graph_id}/reject")
def reject_relationship_graph_version(
    graph_id: str,
    payload: RelationshipGraphRejectRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_graph_command(
        session,
        graph=_graph_or_http_404(session, graph_id),
        command_type="REJECT_RELATIONSHIP_GRAPH",
        expected_project_version=payload.expected_project_version,
        expected_graph_version=payload.expected_graph_version,
        actor=payload.actor,
        payload={"note": payload.note, "issues": payload.issues},
        idempotency_key=idempotency_key,
        route_key=f"relationship-graphs:reject:{graph_id}",
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/relationship-graphs/{graph_id}/approve")
def approve_relationship_graph_version(
    graph_id: str,
    payload: RelationshipGraphActionRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_graph_command(
        session,
        graph=_graph_or_http_404(session, graph_id),
        command_type="APPROVE_RELATIONSHIP_GRAPH",
        expected_project_version=payload.expected_project_version,
        expected_graph_version=payload.expected_graph_version,
        actor=payload.actor,
        payload={"note": payload.note},
        idempotency_key=idempotency_key,
        route_key=f"relationship-graphs:approve:{graph_id}",
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/relationship-graphs/{graph_id}/revisions", status_code=status.HTTP_201_CREATED)
def create_relationship_graph_revision_version(
    graph_id: str,
    payload: RelationshipGraphRevisionRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_graph_command(
        session,
        graph=_graph_or_http_404(session, graph_id),
        command_type="CREATE_RELATIONSHIP_GRAPH_REVISION",
        expected_project_version=payload.expected_project_version,
        expected_graph_version=None,
        actor=payload.actor,
        payload={"note": payload.note},
        idempotency_key=idempotency_key,
        route_key=f"relationship-graphs:revision:{graph_id}",
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/relationship-graphs/{graph_id}/relationships/{relationship_key}/lock")
def lock_relationship(
    graph_id: str,
    relationship_key: str,
    payload: RelationshipGraphActionRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_graph_command(
        session,
        graph=_graph_or_http_404(session, graph_id),
        command_type="SET_RELATIONSHIP_LOCK",
        expected_project_version=payload.expected_project_version,
        expected_graph_version=payload.expected_graph_version,
        actor=payload.actor,
        payload={"relationship_key": relationship_key, "locked": True, "note": payload.note},
        idempotency_key=idempotency_key,
        route_key=f"relationship-graphs:lock:{graph_id}:{relationship_key}",
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post("/relationship-graphs/{graph_id}/relationships/{relationship_key}/unlock")
def unlock_relationship(
    graph_id: str,
    relationship_key: str,
    payload: RelationshipGraphActionRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_graph_command(
        session,
        graph=_graph_or_http_404(session, graph_id),
        command_type="SET_RELATIONSHIP_LOCK",
        expected_project_version=payload.expected_project_version,
        expected_graph_version=payload.expected_graph_version,
        actor=payload.actor,
        payload={"relationship_key": relationship_key, "locked": False, "note": payload.note},
        idempotency_key=idempotency_key,
        route_key=f"relationship-graphs:unlock:{graph_id}:{relationship_key}",
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)
