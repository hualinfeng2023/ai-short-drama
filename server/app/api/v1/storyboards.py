from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.models import StoryboardVersion
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import StoryboardShotRegenerateRequest, StoryPackageGenerateRequest
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash
from app.services.storyboards_v2 import (
    list_workflow_runs,
    regenerate_storyboard_shot,
    storyboard_workspace,
)

router = APIRouter(prefix="/api/v1", tags=["storyboards"])


@router.get("/projects/{project_id}/storyboard-workspace")
def get_storyboard_workspace(
    project_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(storyboard_workspace(session, project_id))


@router.get("/projects/{project_id}/workflow-runs")
def workflow_runs(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_workflow_runs(session, project_id))


@router.post(
    "/shot-specs/{shot_spec_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
)
def regenerate_shot_spec(
    shot_spec_id: str,
    payload: StoryboardShotRegenerateRequest,
    response: Response,
    _idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    shot, job, replayed = regenerate_storyboard_shot(
        session,
        shot_spec_id=shot_spec_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
        trace_id=get_trace_id(),
        note=payload.note,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success({"shot": shot, "job": job})


@router.post(
    "/storyboards/{storyboard_id}/approve",
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_storyboard_version(
    storyboard_id: str,
    payload: StoryPackageGenerateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    storyboard = session.get(StoryboardVersion, storyboard_id)
    if storyboard is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜版本不存在"},
        )
    command_id = str(
        uuid5(NAMESPACE_URL, f"{storyboard.project_id}:domain-command:{idempotency_key}")
    )
    execution = dispatch_domain_command(
        session,
        project_id=storyboard.project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="APPROVE_STORYBOARD",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=storyboard.id,
            target_version_id=storyboard.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=storyboard.id,
                target_hash=storyboard.content_hash,
            ),
            payload={"confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"storyboard-approval:{storyboard.id}",
                "expected_version": payload.expected_version,
                "actor": payload.actor,
                "confirmed": True,
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(
        execution.idempotency_replayed
    ).lower()
    return success(execution.result)
