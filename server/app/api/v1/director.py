import json
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.trace import success
from app.config import get_settings
from app.db.models import IdempotencyKey, ScriptVersion
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.domain.director import (
    DirectorProposalDecisionRequest,
    DirectorProposalExecuteRequest,
    DirectorProposalRequest,
)
from app.services.director_proposals import (
    director_proposal_or_404,
    list_director_proposals,
    prepare_director_proposal,
)
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash

router = APIRouter(prefix="/api/v1", tags=["director"])


def _command_id(project_id: str, idempotency_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{project_id}:director-command:{idempotency_key}"))


def _proposal_replay(
    session: Session,
    *,
    project_id: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> dict[str, object] | None:
    record = session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.scope == f"domain-command:{project_id}",
            IdempotencyKey.key == idempotency_key,
        )
    )
    if record is None:
        return None
    command_id = _command_id(project_id, idempotency_key)
    expected_hash = content_hash(
        {
            "project_id": project_id,
            "command_id": command_id,
            "command_type": "CREATE_DIRECTOR_PROPOSAL",
            "actor": {"type": "DIRECTOR", "id": "ai-director"},
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
        }
    )
    if record.request_hash != expected_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_CONFLICT",
                "message": "该幂等键已用于不同的 Director 审查请求",
            },
        )
    stored = json.loads(record.response_json)
    return dict(stored["result"])


@router.post("/projects/{project_id}/director-review-proposals", status_code=201)
async def create_director_proposal(
    project_id: str,
    payload: DirectorProposalRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    request_fingerprint = content_hash(payload.model_dump(mode="json"))
    replay = _proposal_replay(
        session,
        project_id=project_id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return success(replay)
    draft = await prepare_director_proposal(
        session,
        get_settings(),
        project_id=project_id,
        request=payload,
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=_command_id(project_id, idempotency_key),
            command_type="CREATE_DIRECTOR_PROPOSAL",
            actor=CommandActor(type="DIRECTOR", id="ai-director"),
            target_object_id=draft.target_object_id,
            target_version_id=draft.target_version_id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=draft.target_version_id,
                target_hash=draft.target_hash,
            ),
            payload=draft.payload,
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=request_fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/director-review-proposals/{proposal_id}")
def get_director_proposal(
    proposal_id: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(director_proposal_or_404(session, proposal_id))


@router.get("/projects/{project_id}/director-review-proposals")
def get_project_director_proposals(
    project_id: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(list_director_proposals(session, project_id=project_id))


@router.post("/director-review-proposals/{proposal_id}/execute")
def execute_director_proposal(
    proposal_id: str,
    payload: DirectorProposalExecuteRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    proposal = director_proposal_or_404(session, proposal_id)
    project_id = str(proposal["project_id"])
    script_id = str(proposal["target_objects"][0]["version_id"])
    script = session.get(ScriptVersion, script_id)
    if script is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIRECTOR_BASE_VERSION_MISSING", "message": "基础剧本版本不存在"},
        )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=_command_id(project_id, idempotency_key),
            command_type="APPLY_DIRECTOR_PROPOSAL",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=proposal_id,
            target_version_id=script.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=script.id,
                target_hash=script.content_hash,
            ),
            payload={
                "option_id": payload.option_id,
                "confirmed": payload.confirmed,
            },
            idempotency_key=idempotency_key,
        ),
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.post("/director-review-proposals/{proposal_id}/decision")
def decide_director_proposal(
    proposal_id: str,
    payload: DirectorProposalDecisionRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    proposal = director_proposal_or_404(session, proposal_id)
    project_id = str(proposal["project_id"])
    target_version_id = (
        str(proposal["result_script_version_id"])
        if payload.decision in {"APPROVE", "ROLLBACK"}
        else str(proposal["target_objects"][0]["version_id"])
    )
    script = session.get(ScriptVersion, target_version_id)
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=_command_id(project_id, idempotency_key),
            command_type="DECIDE_DIRECTOR_PROPOSAL",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=proposal_id,
            target_version_id=target_version_id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=target_version_id,
                target_hash=script.content_hash if script is not None else None,
            ),
            payload={
                "decision": payload.decision,
                "confirmed": payload.confirmed,
            },
            idempotency_key=idempotency_key,
        ),
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)
