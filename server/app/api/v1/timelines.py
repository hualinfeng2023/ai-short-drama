from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.session import get_session
from app.services.multitrack_timeline import get_timeline_workspace

router = APIRouter(prefix="/api/v1", tags=["timelines"])


@router.get("/projects/{project_id}/timeline-workspace")
def timeline_workspace(
    project_id: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(get_timeline_workspace(session, project_id))
