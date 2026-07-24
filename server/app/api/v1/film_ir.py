from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.session import get_session
from app.services.film_ir import get_film_ir_projection
from app.services.workspace import project_or_404

router = APIRouter(prefix="/api/v1", tags=["film-ir"])


@router.get("/projects/{project_id}/film-ir")
def project_film_ir(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    project = project_or_404(session, project_id)
    return success(get_film_ir_projection(session, project))
