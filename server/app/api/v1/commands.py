from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.session import get_session
from app.domain.commands import DirectorCommand
from app.services.domain_commands import dispatch_domain_command

router = APIRouter(prefix="/api/v1", tags=["commands"])


@router.post("/projects/{project_id}/commands")
def execute_domain_command(
    project_id: str,
    payload: DirectorCommand,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=payload,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.as_dict())
