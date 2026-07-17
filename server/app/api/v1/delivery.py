from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import ExportMatrixRequest, ExportProfileCreate
from app.services.delivery import (
    create_export_matrix,
    create_export_profile,
    list_export_profiles,
)

router = APIRouter(prefix="/api/v1", tags=["delivery"])


@router.post("/projects/{project_id}/export-profiles", status_code=status.HTTP_201_CREATED)
def create_profile(
    project_id: str,
    payload: ExportProfileCreate,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(create_export_profile(session, project_id=project_id, payload=payload))


@router.get("/projects/{project_id}/export-profiles")
def profiles(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_export_profiles(session, project_id))


@router.post("/projects/{project_id}/exports/matrix", status_code=status.HTTP_202_ACCEPTED)
def export_matrix(
    project_id: str,
    payload: ExportMatrixRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        create_export_matrix(
            session,
            project_id=project_id,
            payload=payload,
            trace_id=get_trace_id(),
        )
    )
