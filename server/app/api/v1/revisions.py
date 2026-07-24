from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.models import Project, TimelineVersion
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import (
    PreviewApprovalRequest,
    PreviewRollbackRequest,
    RevisionCreateRequest,
    RevisionImpactRequest,
)
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash
from app.services.revisions import (
    analyze_revision,
    compare_timelines,
    get_timeline,
    revision_or_404,
)

router = APIRouter(prefix="/api/v1", tags=["revision"])


def _dispatch_preview_command(
    session: Session,
    *,
    timeline_id: str,
    expected_version: int,
    actor: str,
    command_type: str,
    idempotency_key: str,
) -> tuple[dict[str, object], bool]:
    timeline = session.get(TimelineVersion, timeline_id)
    if timeline is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Preview 版本不存在"},
        )
    command_id = str(
        uuid5(NAMESPACE_URL, f"{timeline.project_id}:domain-command:{idempotency_key}")
    )
    execution = dispatch_domain_command(
        session,
        project_id=timeline.project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type=command_type,
            actor=CommandActor(type="USER", id=actor),
            target_object_id=timeline.project_id,
            target_version_id=timeline.id,
            expected_version=ExpectedVersion(
                project_lock_version=expected_version,
                target_version_id=timeline.id,
                target_hash=timeline.baseline_hash,
            ),
            payload={"confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"preview:{timeline.id}:{command_type}",
                "expected_version": expected_version,
                "actor": actor,
                "confirmed": True,
            }
        ),
    )
    return execution.result, execution.idempotency_replayed


@router.get("/previews/{timeline_id}")
def preview(timeline_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(get_timeline(session, timeline_id))


@router.post("/previews/{timeline_id}/approve")
def approve_preview(
    timeline_id: str,
    payload: PreviewApprovalRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_preview_command(
        session,
        timeline_id=timeline_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
        command_type="APPROVE_PREVIEW",
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.get("/previews/{left_id}/compare/{right_id}")
def compare_preview(
    left_id: str, right_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(compare_timelines(session, left_id, right_id))


@router.post("/projects/{project_id}/revision-impact")
def revision_impact(
    project_id: str,
    payload: RevisionImpactRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        analyze_revision(
            session,
            project_id=project_id,
            expected_version=payload.expected_version,
            scope=payload.scope,
            instruction=payload.instruction,
        )
    )


@router.post(
    "/projects/{project_id}/revisions",
    status_code=status.HTTP_202_ACCEPTED,
)
def start_revision(
    project_id: str,
    payload: RevisionCreateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = session.get(Project, project_id)
    timeline = (
        session.get(TimelineVersion, project.current_timeline_version_id)
        if project is not None and project.current_timeline_version_id is not None
        else None
    )
    if project is None or timeline is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "PREVIEW_REQUIRED", "message": "创建变更集前必须先生成 Preview"},
        )
    command_id = str(
        uuid5(NAMESPACE_URL, f"{project.id}:domain-command:{idempotency_key}")
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="CREATE_REVISION_CHANGE_SET",
            actor=CommandActor(type="USER", id="demo-user"),
            target_object_id=project.id,
            target_version_id=timeline.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=timeline.id,
                target_hash=timeline.baseline_hash,
            ),
            payload=payload.model_dump(
                mode="json",
                exclude={"expected_version"},
            ),
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"revision-change-set:{project.id}",
                "expected_version": payload.expected_version,
                "payload": payload.model_dump(
                    mode="json",
                    exclude={"expected_version"},
                ),
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(
        execution.idempotency_replayed
    ).lower()
    return success(execution.result)


@router.get("/revisions/{change_set_id}")
def revision(change_set_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(revision_or_404(session, change_set_id))


@router.post("/previews/{timeline_id}/rollback")
def rollback_preview(
    timeline_id: str,
    payload: PreviewRollbackRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_preview_command(
        session,
        timeline_id=timeline_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
        command_type="ROLLBACK_PREVIEW",
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)
