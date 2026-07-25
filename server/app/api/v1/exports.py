from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import ExportCreateRequest, ExportEstimateRequest
from app.services.domain_commands import dispatch_domain_command
from app.services.exports import estimate_export, get_export, list_exports
from app.services.projects import content_hash
from app.services.workspace import project_or_404

router = APIRouter(prefix="/api/v1", tags=["export"])


@router.post("/projects/{project_id}/exports/estimate")
def export_estimate(
    project_id: str,
    payload: ExportEstimateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(estimate_export(session, project_id=project_id, profile=payload.profile))


@router.post("/projects/{project_id}/exports", status_code=status.HTTP_202_ACCEPTED)
def export_project(
    project_id: str,
    payload: ExportCreateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    target_version_id = project.current_timeline_version_id or project.id
    fingerprint = content_hash(
        {
            "route": f"exports:create:{project.id}",
            "expected_version": payload.expected_version,
            "profile": payload.profile,
            "rights_confirmed": payload.rights_confirmed,
            "actor": payload.actor,
            "timeline_id": target_version_id,
        }
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=str(uuid5(NAMESPACE_URL, f"{project.id}:CREATE_EXPORT:{idempotency_key}")),
            command_type="CREATE_EXPORT",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=project.id,
            target_version_id=target_version_id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=target_version_id,
            ),
            payload={
                "profile": payload.profile,
                "rights_confirmed": payload.rights_confirmed,
                "confirmed": True,
            },
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/projects/{project_id}/exports")
def exports(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_exports(session, project_id))


@router.get("/exports/{export_id}")
def export(export_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(get_export(session, export_id))
