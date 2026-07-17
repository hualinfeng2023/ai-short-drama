from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.session import get_session
from app.services.audio_pipeline import get_audio_workspace

router = APIRouter(prefix="/api/v1", tags=["audio"])


@router.get("/projects/{project_id}/audio-workspace")
def audio_workspace(
    project_id: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(get_audio_workspace(session, project_id))
