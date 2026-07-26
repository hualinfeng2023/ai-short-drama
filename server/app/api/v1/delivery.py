from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import ExportMatrixRequest, ExportProfileCreate
from app.services.delivery import list_export_profiles
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash
from app.services.workspace import project_or_404

router = APIRouter(prefix="/api/v1", tags=["delivery"])


@router.post("/projects/{project_id}/export-profiles", status_code=status.HTTP_201_CREATED)
def create_profile(
    project_id: str,
    payload: ExportProfileCreate,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    profile_payload = payload.model_dump(
        mode="json",
        exclude={"expected_version", "actor"},
    )
    fingerprint = content_hash(
        {
            "route": f"export-profiles:create:{project.id}",
            "expected_version": payload.expected_version,
            "profile": profile_payload,
            "actor": payload.actor,
        }
    )
    effective_key = idempotency_key or f"export-profile-{fingerprint}"
    execution = dispatch_domain_command(
        session,
        project_id=project.id,
        command=DirectorCommand(
            command_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project.id}:CREATE_EXPORT_PROFILE:{effective_key}",
                )
            ),
            command_type="CREATE_EXPORT_PROFILE",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=project.id,
            target_version_id=project.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=project.id,
            ),
            payload={"profile": profile_payload, "confirmed": True},
            idempotency_key=effective_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/projects/{project_id}/export-profiles")
def profiles(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_export_profiles(session, project_id))


@router.post("/projects/{project_id}/exports/matrix", status_code=status.HTTP_202_ACCEPTED)
def export_matrix(
    project_id: str,
    payload: ExportMatrixRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    target_version_id = project.current_timeline_version_id or project.id
    matrix_payload = payload.model_dump(
        mode="json",
        exclude={"expected_version", "actor"},
    )
    fingerprint = content_hash(
        {
            "route": f"exports:matrix:{project.id}",
            "expected_version": payload.expected_version,
            "timeline_id": target_version_id,
            "matrix": matrix_payload,
            "actor": payload.actor,
        }
    )
    effective_key = idempotency_key or f"export-matrix-{fingerprint}"
    execution = dispatch_domain_command(
        session,
        project_id=project.id,
        command=DirectorCommand(
            command_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project.id}:CREATE_EXPORT_MATRIX:{effective_key}",
                )
            ),
            command_type="CREATE_EXPORT_MATRIX",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=project.id,
            target_version_id=target_version_id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=target_version_id,
            ),
            payload={"matrix": matrix_payload, "confirmed": True},
            idempotency_key=effective_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result["exports"])
