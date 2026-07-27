import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
    list_director_generation_failures,
    list_director_generation_history,
    list_director_proposals,
    prepare_director_proposal,
    record_director_generation_failure,
)
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import canonical_json, content_hash

router = APIRouter(prefix="/api/v1", tags=["director"])
logger = logging.getLogger(__name__)


def _command_id(project_id: str, idempotency_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{project_id}:director-command:{idempotency_key}"))


def _request_reservation_id(project_id: str, idempotency_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{project_id}:director-request:{idempotency_key}"))


def _request_reservation_scope(project_id: str) -> str:
    return f"director-request:{project_id}"


def _idempotency_conflict(idempotency_key: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "IDEMPOTENCY_CONFLICT",
            "message": "该幂等键已用于不同的 Director 审查请求",
            "retryable": False,
            "details": {"idempotency_key": idempotency_key},
        },
    )


def _replay_request_reservation(
    record: IdempotencyKey,
    *,
    request_fingerprint: str,
    idempotency_key: str,
) -> dict[str, object] | None:
    if record.request_hash != request_fingerprint:
        raise _idempotency_conflict(idempotency_key)
    stored = json.loads(record.response_json)
    state = stored.get("state")
    if state == "SUCCEEDED" and isinstance(stored.get("result"), dict):
        return dict(stored["result"])
    if state == "FAILED":
        detail = stored.get("error")
        raise HTTPException(
            status_code=record.status_code,
            detail=(
                detail
                if isinstance(detail, dict)
                else {
                    "code": "DIRECTOR_REQUEST_FAILED",
                    "message": "Director 审查请求已失败",
                    "retryable": False,
                }
            ),
            headers={"Idempotency-Replayed": "true"},
        )
    raise HTTPException(
        status_code=409,
        detail={
            "code": "DIRECTOR_REQUEST_IN_PROGRESS",
            "message": "相同的 Director 审查请求正在执行，请勿重复提交",
            "retryable": True,
            "details": {"idempotency_key": idempotency_key},
        },
        headers={"Idempotency-Replayed": "true"},
    )


def _reservation_expired(record: IdempotencyKey) -> bool:
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


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
        reservation = session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.scope == _request_reservation_scope(project_id),
                IdempotencyKey.key == idempotency_key,
            )
        )
        if reservation is not None and _reservation_expired(reservation):
            session.delete(reservation)
            session.commit()
            return None
        return (
            _replay_request_reservation(
                reservation,
                request_fingerprint=request_fingerprint,
                idempotency_key=idempotency_key,
            )
            if reservation is not None
            else None
        )
    expected_hash = content_hash(
        {
            "project_id": project_id,
            "command_id": _command_id(project_id, idempotency_key),
            "command_type": "CREATE_DIRECTOR_PROPOSAL",
            "actor": {"type": "DIRECTOR", "id": "ai-director"},
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
        }
    )
    if record.request_hash != expected_hash:
        raise _idempotency_conflict(idempotency_key)
    stored = json.loads(record.response_json)
    return dict(stored["result"])


def _reserve_director_request(
    session: Session,
    *,
    project_id: str,
    idempotency_key: str,
    request_fingerprint: str,
    ttl_seconds: int = 1800,
) -> tuple[IdempotencyKey | None, dict[str, object] | None]:
    now = datetime.now(UTC)
    reservation = IdempotencyKey(
        id=_request_reservation_id(project_id, idempotency_key),
        scope=_request_reservation_scope(project_id),
        key=idempotency_key,
        request_hash=request_fingerprint,
        response_json=canonical_json({"state": "IN_PROGRESS"}),
        status_code=202,
        resource_id=project_id,
        created_at=now,
        expires_at=now + timedelta(seconds=max(900, ttl_seconds)),
    )
    session.add(reservation)
    try:
        session.commit()
        return reservation, None
    except IntegrityError:
        session.rollback()
        winner = session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.scope == _request_reservation_scope(project_id),
                IdempotencyKey.key == idempotency_key,
            )
        )
        if winner is None:
            raise
        replayed = _replay_request_reservation(
            winner,
            request_fingerprint=request_fingerprint,
            idempotency_key=idempotency_key,
        )
        return None, replayed


def _complete_director_request(
    session: Session,
    *,
    reservation_id: str,
    result: dict[str, object],
) -> None:
    reservation = session.get(IdempotencyKey, reservation_id)
    if reservation is None:
        return
    reservation.response_json = canonical_json({"state": "SUCCEEDED", "result": result})
    reservation.status_code = 201
    reservation.expires_at = datetime.now(UTC) + timedelta(days=7)
    proposal_id = result.get("proposal_id")
    if isinstance(proposal_id, str):
        reservation.resource_id = proposal_id
    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Director 成功幂等记录更新失败：reservation_id=%s", reservation_id)


def _fail_director_request(
    session: Session,
    *,
    reservation_id: str,
    exc: HTTPException,
) -> None:
    reservation = session.get(IdempotencyKey, reservation_id)
    if reservation is None:
        return
    detail = exc.detail
    reservation.response_json = canonical_json(
        {
            "state": "FAILED",
            "error": (
                detail
                if isinstance(detail, dict)
                else {
                    "code": "DIRECTOR_REQUEST_FAILED",
                    "message": str(detail),
                    "retryable": False,
                }
            ),
        }
    )
    reservation.status_code = exc.status_code
    reservation.expires_at = datetime.now(UTC) + timedelta(days=7)
    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Director 失败幂等记录更新失败：reservation_id=%s", reservation_id)


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
    settings = get_settings()
    reservation, reservation_replay = _reserve_director_request(
        session,
        project_id=project_id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        ttl_seconds=int(settings.ark_request_timeout_seconds * 3) + 120,
    )
    if reservation_replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return success(reservation_replay)
    if reservation is None:  # pragma: no cover - guarded by reservation state handling
        raise RuntimeError("Director 幂等占位缺少执行记录")
    try:
        draft = await prepare_director_proposal(
            session,
            settings,
            project_id=project_id,
            request=payload,
        )
        command_id = _command_id(project_id, idempotency_key)
        command = DirectorCommand(
            command_id=command_id,
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
        )
        try:
            execution = dispatch_domain_command(
                session,
                project_id=project_id,
                command=command,
                request_fingerprint=request_fingerprint,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if detail.get("code") == "DIRECTOR_CHANGE_CONTRACT_INVALID":
                session.rollback()
                provider = draft.payload.get("provider")
                provider_payload = provider if isinstance(provider, dict) else {}
                error_details = detail.get("details")
                try:
                    record_director_generation_failure(
                        session,
                        project_id=project_id,
                        script_scene_id=draft.target_object_id,
                        script_version_id=draft.target_version_id,
                        actor=payload.actor,
                        provider=str(provider_payload.get("provider") or "unknown"),
                        model=str(provider_payload.get("model") or "unknown"),
                        prompt_source={
                            "context": draft.payload.get("context"),
                            "instruction": draft.payload.get("instruction"),
                        },
                        error_code=str(detail["code"]),
                        error_message=str(detail.get("message") or exc),
                        retryable=False,
                        details=error_details if isinstance(error_details, dict) else {},
                        latency_ms=(
                            int(provider_payload["latency_ms"])
                            if isinstance(provider_payload.get("latency_ms"), (int, float))
                            else None
                        ),
                        stage="COMMAND_VALIDATION",
                        command_id=command_id,
                        retry_of_generation_record_id=(
                            str(draft.payload["retry_of_generation_record_id"])
                            if isinstance(
                                draft.payload.get("retry_of_generation_record_id"),
                                str,
                            )
                            else None
                        ),
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                    logger.exception(
                        "Director Command 校验失败观测记录写入失败：project_id=%s",
                        project_id,
                    )
            raise
    except HTTPException as exc:
        _fail_director_request(
            session,
            reservation_id=reservation.id,
            exc=exc,
        )
        raise
    _complete_director_request(
        session,
        reservation_id=reservation.id,
        result=execution.result,
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


@router.get("/projects/{project_id}/director-generation-failures")
def get_project_director_generation_failures(
    project_id: str,
    script_scene_id: str | None = Query(default=None, min_length=36, max_length=36),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        list_director_generation_failures(
            session,
            project_id=project_id,
            script_scene_id=script_scene_id,
        )
    )


@router.get("/projects/{project_id}/director-generation-history")
def get_project_director_generation_history(
    project_id: str,
    script_scene_id: str | None = Query(default=None, min_length=36, max_length=36),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        list_director_generation_history(
            session,
            project_id=project_id,
            script_scene_id=script_scene_id,
        )
    )


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
                "intent_confirmation_token": payload.intent_confirmation_token,
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
                "override_reason": payload.override_reason,
            },
            idempotency_key=idempotency_key,
        ),
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)
