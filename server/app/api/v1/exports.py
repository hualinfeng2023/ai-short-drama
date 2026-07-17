from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.trace import get_trace_id, success
from app.db.session import get_session
from app.schemas import ExportCreateRequest, ExportEstimateRequest
from app.services.exports import create_export, estimate_export, get_export, list_exports

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
    export, job, replayed = create_export(
        session,
        project_id=project_id,
        expected_version=payload.expected_version,
        profile=payload.profile,
        rights_confirmed=payload.rights_confirmed,
        actor=payload.actor,
        idempotency_key=idempotency_key,
        trace_id=get_trace_id(),
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success({"export": export, "job": job})


@router.get("/projects/{project_id}/exports")
def exports(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_exports(session, project_id))


@router.get("/exports/{export_id}")
def export(export_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(get_export(session, export_id))
