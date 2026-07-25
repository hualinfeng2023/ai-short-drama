from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import StoryPackageGenerateRequest
from app.services.domain_commands import dispatch_domain_command
from app.services.preproduction import preproduction_workspace
from app.services.projects import content_hash
from app.services.workspace import project_or_404

router = APIRouter(prefix="/api/v1", tags=["preproduction"])


@router.get("/projects/{project_id}/preproduction")
def get_preproduction(
    project_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(preproduction_workspace(session, project_id))


@router.post(
    "/projects/{project_id}/preproduction/approve",
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_visual_bible(
    project_id: str,
    payload: StoryPackageGenerateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    target_version_id = project.current_story_version_id or project.id
    fingerprint = content_hash(
        {
            "route": f"preproduction:approve:{project.id}",
            "expected_version": payload.expected_version,
            "actor": payload.actor,
            "target_version_id": target_version_id,
        }
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project.id}:APPROVE_PREPRODUCTION:{idempotency_key}",
                )
            ),
            command_type="APPROVE_PREPRODUCTION",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=project.id,
            target_version_id=target_version_id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=target_version_id,
            ),
            payload={"confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)
