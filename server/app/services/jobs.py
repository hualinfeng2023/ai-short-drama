import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import exists, or_, select, update
from sqlalchemy.orm import Session, aliased, selectinload

from app.db.models import (
    ChangeSet,
    Character,
    CharacterCandidateBatch,
    CharacterIdentityVersion,
    ExportRecord,
    Job,
    JobDependency,
    Project,
    ProposalVersion,
    Shot,
    Take,
    TimelineVersion,
    UsageLedger,
    WorkerState,
)
from app.schemas import JobRead, JobRecoveryRequest
from app.services.events import append_event
from app.services.projects import canonical_json
from app.services.workspace import not_found, project_or_404

QUEUED_STATUSES = {"PENDING", "RETRY_WAIT"}
ACTIVE_STATUSES = {"RUNNING", "CANCEL_REQUESTED"}
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
RETRY_DELAYS = (5, 20, 60)
RECOVERY_REQUEUE_ACTIONS = {
    "RESUME_FROM_FAILURE",
    "RETRY_FAILED_PARTS",
    "SWITCH_MODEL",
    "FALLBACK_EXECUTION",
    "PROVIDE_INPUT",
}


def _json_object(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _failure_details_with_recovery(
    job: Job,
    details: dict[str, object] | None,
) -> dict[str, object]:
    merged = dict(details or {})
    failed_parts = _string_list(merged.get("failed_parts"))
    if not failed_parts and job.entity_type == "shot":
        failed_parts = [job.entity_id]
    completed_steps = _string_list(merged.get("completed_steps"))
    unreliable_outputs = _string_list(merged.get("unreliable_outputs"))
    output = _json_object(job.output_json)
    failed_step = merged.get("failed_step")
    if not isinstance(failed_step, str) or not failed_step.strip():
        failed_step = job.stage
    if not unreliable_outputs:
        unreliable_outputs = [f"{failed_step}及其后续结果尚未完成验证"]

    actions = [
        "SAVE_INTERMEDIATE",
        "PROVIDE_INPUT",
        "SWITCH_MODEL",
        "FALLBACK_EXECUTION",
    ]
    if job.retryable:
        actions.insert(0, "RESUME_FROM_FAILURE")
    if failed_parts:
        actions.insert(1 if job.retryable else 0, "RETRY_FAILED_PARTS")

    merged["recovery"] = {
        "completion_state": "PARTIAL" if job.progress > 0 else "NOT_COMPLETED",
        "completed_percent": round(job.progress),
        "failed_step": failed_step,
        "completed_steps": completed_steps,
        "failed_parts": failed_parts,
        "intermediate_result_saved": bool(output),
        "intermediate_result_keys": sorted(output),
        "available_actions": actions,
        "unreliable_outputs": unreliable_outputs,
        "reliability_note": (
            "已完成步骤与中间结果可以保留，但失败步骤及其下游结果不能视为最终可信结果。"
        ),
    }
    return merged


def release_project_after_terminal_job(session: Session, job: Job, now: datetime) -> None:
    running_status: str
    fallback_status: str
    if job.job_type == "GENERATE_PROPOSAL":
        running_status = "PROPOSAL_RUNNING"
        fallback_status = "DRAFT"
    elif job.job_type == "GENERATE_STORY_DIRECTIONS":
        running_status = "PROPOSAL_RUNNING"
        has_ready_direction = session.scalar(
            select(ProposalVersion.id)
            .where(
                ProposalVersion.project_id == job.project_id,
                ProposalVersion.status == "READY",
            )
            .limit(1)
        )
        fallback_status = "PROPOSAL_READY" if has_ready_direction is not None else "DRAFT"
    elif job.job_type == "GENERATE_STORY_PACKAGE":
        running_status = "STORY_PACKAGE_RUNNING"
        fallback_status = "PROPOSAL_READY"
    elif job.job_type == "GENERATE_STORY_STRUCTURE":
        running_status = "STORY_STRUCTURE_RUNNING"
        fallback_status = "PROPOSAL_READY"
    elif job.job_type == "GENERATE_SCRIPT_PACKAGE":
        running_status = "SCRIPT_PACKAGE_RUNNING"
        fallback_status = "CHARACTER_VISUAL_READY"
    else:
        return
    project = session.get(Project, job.project_id)
    if project is not None and project.status == running_status:
        project.status = fallback_status
        project.lock_version += 1
        project.updated_at = now


def _restore_project_from_current_timeline(
    session: Session, project: Project, now: datetime
) -> None:
    timeline = (
        session.get(TimelineVersion, project.current_timeline_version_id)
        if project.current_timeline_version_id
        else None
    )
    approved = timeline is not None and timeline.status == "APPROVED"
    project.status = "APPROVED" if approved else "PREVIEW_READY"
    project.preview_approved = approved
    project.export_ready = False
    project.lock_version += 1
    project.updated_at = now


def _release_revision_job(session: Session, job: Job, now: datetime) -> None:
    if job.job_type != "APPLY_REVISION":
        return
    change_set = session.get(ChangeSet, job.entity_id)
    if change_set is None or change_set.result_timeline_id is not None:
        return
    terminal_status = "CANCELLED" if job.status == "CANCELLED" else "FAILED"
    if change_set.status == terminal_status:
        return
    change_set.status = terminal_status
    project = session.get(Project, job.project_id)
    if project is not None:
        _restore_project_from_current_timeline(session, project, now)


def _export_ledger_totals(session: Session, job_id: str) -> tuple[int, int]:
    entries = session.scalars(select(UsageLedger).where(UsageLedger.job_id == job_id)).all()
    reserved = sum(item.points for item in entries if item.entry_type == "RESERVED")
    released = sum(item.points for item in entries if item.entry_type == "RELEASED")
    return reserved, released


def _release_export_job(session: Session, job: Job, now: datetime) -> None:
    if job.job_type != "EXPORT_PACKAGE":
        return
    export = session.get(ExportRecord, job.entity_id)
    if export is None or export.status == "READY":
        return
    terminal_status = "CANCELLED" if job.status == "CANCELLED" else "FAILED"
    if export.status == terminal_status:
        return
    export.status = terminal_status
    export.completed_at = now
    project = session.get(Project, job.project_id)
    if project is None:
        return
    committed = session.scalar(
        select(UsageLedger).where(
            UsageLedger.job_id == job.id,
            UsageLedger.entry_type == "COMMITTED",
        )
    )
    reserved, released = _export_ledger_totals(session, job.id)
    outstanding = max(0, reserved - released)
    if committed is None and outstanding:
        project.available_points += outstanding
        session.add(
            UsageLedger(
                id=str(uuid4()),
                project_id=project.id,
                job_id=job.id,
                entry_type="RELEASED",
                points=outstanding,
                description="导出失败或取消，释放预留积分",
                created_at=now,
            )
        )
    _restore_project_from_current_timeline(session, project, now)


def release_entity_after_terminal_job(session: Session, job: Job, now: datetime) -> None:
    release_project_after_terminal_job(session, job, now)
    _release_revision_job(session, job, now)
    _release_export_job(session, job, now)
    if job.job_type in {
        "GENERATE_CHARACTER_VISUAL_CANDIDATE",
        "GENERATE_CHARACTER_IDENTITY_DOSSIER",
    }:
        from app.services.character_visuals import mark_character_generation_failed

        mark_character_generation_failed(session, job)
    if job.job_type != "GENERATE_SHOT_IMAGE":
        return
    shot = session.get(Shot, job.entity_id)
    if shot is None:
        return
    take_version = int(json.loads(job.input_json).get("take_version", 0))
    materialized = session.scalar(
        select(Take).where(Take.shot_id == shot.id, Take.version == take_version)
    )
    if materialized is None:
        shot.candidate_take = None
        shot.status = "APPROVED" if shot.current_take_id else "READY"
        shot.lock_version += 1


def mark_project_job_running(session: Session, job: Job, now: datetime) -> None:
    if job.job_type in {"GENERATE_PROPOSAL", "GENERATE_STORY_DIRECTIONS"}:
        running_status = "PROPOSAL_RUNNING"
    elif job.job_type == "GENERATE_STORY_PACKAGE":
        running_status = "STORY_PACKAGE_RUNNING"
    elif job.job_type == "GENERATE_STORY_STRUCTURE":
        running_status = "STORY_STRUCTURE_RUNNING"
    elif job.job_type == "GENERATE_SCRIPT_PACKAGE":
        running_status = "SCRIPT_PACKAGE_RUNNING"
    else:
        return
    project = session.get(Project, job.project_id)
    if project is not None and project.status != running_status:
        project.status = running_status
        project.lock_version += 1
        project.updated_at = now


def reconcile_terminal_project_jobs(session: Session) -> int:
    """Repair projects left in a running state after an older terminal job."""
    now = datetime.now(UTC)
    job_types_by_status = {
        "PROPOSAL_RUNNING": ("GENERATE_PROPOSAL", "GENERATE_STORY_DIRECTIONS"),
        "STORY_PACKAGE_RUNNING": ("GENERATE_STORY_PACKAGE",),
        "STORY_STRUCTURE_RUNNING": ("GENERATE_STORY_STRUCTURE",),
        "SCRIPT_PACKAGE_RUNNING": ("GENERATE_SCRIPT_PACKAGE",),
    }
    repaired = 0
    projects = session.scalars(select(Project).where(Project.status.in_(job_types_by_status))).all()
    for project in projects:
        job_types = job_types_by_status[project.status]
        active_job_id = session.scalar(
            select(Job.id)
            .where(
                Job.project_id == project.id,
                Job.job_type.in_(job_types),
                Job.status.in_(QUEUED_STATUSES | ACTIVE_STATUSES),
            )
            .limit(1)
        )
        if active_job_id is not None:
            continue
        terminal_job = session.scalar(
            select(Job)
            .where(
                Job.project_id == project.id,
                Job.job_type.in_(job_types),
                Job.status.in_({"FAILED", "CANCELLED"}),
            )
            .order_by(Job.updated_at.desc())
            .limit(1)
        )
        if terminal_job is None:
            continue
        previous_status = project.status
        release_project_after_terminal_job(session, terminal_job, now)
        if project.status == previous_status:
            continue
        repaired += 1
        append_event(
            session,
            project_id=project.id,
            job_id=terminal_job.id,
            event_type="project.running_state_reconciled",
            payload={
                "job_id": terminal_job.id,
                "job_status": terminal_job.status,
                "previous_project_status": previous_status,
                "project_status": project.status,
            },
        )
    session.commit()
    return repaired


def mark_entity_job_running(session: Session, job: Job, now: datetime) -> None:
    mark_project_job_running(session, job, now)
    project = session.get(Project, job.project_id)
    if job.job_type == "APPLY_REVISION":
        change_set = session.get(ChangeSet, job.entity_id)
        if change_set is not None and change_set.result_timeline_id is None:
            change_set.status = "PENDING"
        if project is not None:
            project.status = "PRODUCING"
            project.preview_approved = False
            project.export_ready = False
            project.lock_version += 1
            project.updated_at = now
    if job.job_type == "EXPORT_PACKAGE":
        export = session.get(ExportRecord, job.entity_id)
        if export is not None and export.status != "READY":
            reserved, released = _export_ledger_totals(session, job.id)
            reserve_amount = next(
                (
                    item.points
                    for item in session.scalars(
                        select(UsageLedger).where(
                            UsageLedger.job_id == job.id,
                            UsageLedger.entry_type == "RESERVED",
                        )
                    )
                ),
                0,
            )
            if reserved <= released and reserve_amount:
                if project is None or project.available_points < reserve_amount:
                    raise HTTPException(
                        status_code=429,
                        detail={
                            "code": "INSUFFICIENT_POINTS",
                            "message": "重试导出所需积分不足",
                            "retryable": False,
                        },
                    )
                project.available_points -= reserve_amount
                session.add(
                    UsageLedger(
                        id=str(uuid4()),
                        project_id=project.id,
                        job_id=job.id,
                        entry_type="RESERVED",
                        points=reserve_amount,
                        description="导出重试预留",
                        created_at=now,
                    )
                )
            export.status = "PENDING"
            export.completed_at = None
            if project is not None:
                project.status = "EXPORTING"
                project.export_ready = False
                project.lock_version += 1
                project.updated_at = now
    if job.job_type == "GENERATE_CHARACTER_IDENTITY_DOSSIER":
        payload = _json_object(job.input_json)
        character = session.get(Character, str(payload.get("character_id", "")))
        identity = session.get(CharacterIdentityVersion, job.entity_id)
        if character is not None:
            character.status = "PENDING_REVIEW"
            character.lock_version += 1
            character.updated_at = now
        if identity is not None:
            identity.status = "GENERATING_DOSSIER"
        return
    if job.job_type == "GENERATE_CHARACTER_VISUAL_CANDIDATE":
        payload = _json_object(job.input_json)
        character = session.get(Character, str(payload.get("character_id", "")))
        batch = session.get(CharacterCandidateBatch, str(payload.get("batch_id", "")))
        if character is not None:
            character.status = "GENERATING"
            character.lock_version += 1
            character.updated_at = now
        if batch is not None:
            batch.status = "GENERATING"
        return
    if job.job_type != "GENERATE_SHOT_IMAGE":
        return
    shot = session.get(Shot, job.entity_id)
    if shot is None:
        return
    shot.status = "GENERATING"
    shot.candidate_take = int(json.loads(job.input_json)["take_version"])
    shot.lock_version += 1


def job_to_read(job: Job) -> JobRead:
    return JobRead.model_validate(job)


def job_or_404(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise not_found("任务", job_id)
    return job


def list_jobs(session: Session) -> list[JobRead]:
    jobs = session.scalars(
        select(Job).options(selectinload(Job.project)).order_by(Job.created_at.desc())
    ).all()
    return [job_to_read(job) for job in jobs]


def list_project_jobs(session: Session, project_id: str) -> list[JobRead]:
    project_or_404(session, project_id)
    jobs = session.scalars(
        select(Job)
        .options(selectinload(Job.project))
        .where(Job.project_id == project_id)
        .order_by(Job.created_at.desc())
    ).all()
    return [job_to_read(job) for job in jobs]


def enqueue_job(
    session: Session,
    *,
    project_id: str,
    job_type: str,
    entity_type: str,
    entity_id: str,
    idempotency_key: str,
    input_payload: dict[str, object],
    label: str,
    stage: str,
    trace_id: str,
    estimated_seconds: int | None = None,
    max_attempts: int = 3,
    priority: int = 0,
    retryable: bool = True,
) -> tuple[Job, bool]:
    request_hash = sha256(canonical_json(input_payload).encode()).hexdigest()
    existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": "相同任务幂等键对应了不同输入",
                    "user_action": "刷新当前实体版本后重新发起",
                    "retryable": False,
                    "details": {"job_id": existing.id},
                },
            )
        return existing, True

    now = datetime.now(UTC)
    job = Job(
        id=str(uuid4()),
        project_id=project_id,
        job_type=job_type,
        entity_type=entity_type,
        entity_id=entity_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        label=label,
        entity=f"{entity_type}:{entity_id}",
        status="PENDING",
        progress=0,
        stage=stage,
        priority=priority,
        attempt=0,
        max_attempts=max_attempts,
        available_at=now,
        lease_until=None,
        heartbeat_at=None,
        cancel_requested=False,
        input_json=canonical_json(input_payload),
        output_json=None,
        error_code=None,
        error_message=None,
        error_details_json=None,
        created_at_label=now.strftime("%H:%M"),
        created_at=now,
        updated_at=now,
        completed_at=None,
        worker_id=None,
        trace_id=trace_id,
        estimated_seconds=estimated_seconds,
        retryable=retryable,
    )
    session.add(job)
    session.flush()
    append_event(
        session,
        project_id=project_id,
        job_id=job.id,
        event_type="job.created",
        payload={"job_id": job.id, "status": job.status, "stage": job.stage, "progress": 0},
    )
    return job, False


def request_cancel(session: Session, job_id: str) -> JobRead:
    job = job_or_404(session, job_id)
    now = datetime.now(UTC)
    if job.status in TERMINAL_STATUSES:
        return job_to_read(job)
    if job.status in QUEUED_STATUSES:
        job.status = "CANCELLED"
        job.cancel_requested = True
        job.stage = "已取消"
        job.completed_at = now
        release_entity_after_terminal_job(session, job, now)
    else:
        job.status = "CANCEL_REQUESTED"
        job.cancel_requested = True
        job.stage = "等待当前处理单元结束"
    job.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="job.cancel_requested",
        payload={"job_id": job.id, "status": job.status, "progress": job.progress},
    )
    session.commit()
    session.refresh(job)
    return job_to_read(job)


def request_retry(session: Session, job_id: str) -> JobRead:
    job = job_or_404(session, job_id)
    if job.status in {"PENDING", "RETRY_WAIT", "RUNNING"}:
        return job_to_read(job)
    if job.status not in {"FAILED", "CANCELLED"} or not job.retryable:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_NOT_RETRYABLE",
                "message": "当前任务不能重试",
                "user_action": "查看错误详情或返回上游修改输入",
                "retryable": False,
                "details": {"job_id": job.id, "status": job.status},
            },
        )
    now = datetime.now(UTC)
    job.status = "RETRY_WAIT"
    job.stage = "等待重试"
    job.available_at = now
    job.lease_until = None
    mark_entity_job_running(session, job, now)
    job.heartbeat_at = None
    job.worker_id = None
    job.cancel_requested = False
    job.completed_at = None
    job.error_code = None
    job.error_message = None
    job.error_details_json = None
    job.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="job.retry_requested",
        payload={"job_id": job.id, "status": job.status, "attempt": job.attempt},
    )
    session.commit()
    session.refresh(job)
    return job_to_read(job)


def request_job_recovery(
    session: Session,
    job_id: str,
    request: JobRecoveryRequest,
) -> JobRead:
    job = job_or_404(session, job_id)
    if job.status not in {"FAILED", "CANCELLED"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_RECOVERY_NOT_AVAILABLE",
                "message": "只有失败或已取消的任务可以发起恢复",
                "user_action": "等待当前任务结束后再选择恢复方式",
                "retryable": False,
                "details": {"job_id": job.id, "status": job.status},
            },
        )

    details = _json_object(job.error_details_json)
    recovery = details.get("recovery")
    if not isinstance(recovery, dict):
        details = _failure_details_with_recovery(job, details)
        recovery = details["recovery"]
    failed_parts = _string_list(recovery.get("failed_parts"))
    if not failed_parts and job.entity_type == "shot":
        failed_parts = [job.entity_id]
        recovery["failed_parts"] = failed_parts
        recovery["available_actions"] = [
            *[
                action
                for action in _string_list(recovery.get("available_actions"))
                if action != "RETRY_FAILED_PARTS"
            ],
            "RETRY_FAILED_PARTS",
        ]
        details["recovery"] = recovery
    available_actions = _string_list(recovery.get("available_actions"))
    if request.action not in available_actions:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_RECOVERY_ACTION_NOT_AVAILABLE",
                "message": "当前任务不支持所选恢复方式",
                "user_action": "选择任务详情中列出的恢复方式",
                "retryable": False,
                "details": {
                    "job_id": job.id,
                    "action": request.action,
                    "available_actions": available_actions,
                },
            },
        )

    now = datetime.now(UTC)
    if request.action == "SAVE_INTERMEDIATE":
        output = _json_object(job.output_json)
        output.setdefault(
            "_checkpoint",
            {
                "progress": job.progress,
                "stage": job.stage,
                "saved_at": now.isoformat(),
            },
        )
        job.output_json = canonical_json(output)
        recovery["intermediate_result_saved"] = True
        recovery["intermediate_result_keys"] = sorted(output)
        recovery["intermediate_result_saved_at"] = now.isoformat()
        details["recovery"] = recovery
        job.error_details_json = canonical_json(details)
        job.stage = "中间结果已保存"
        job.updated_at = now
        append_event(
            session,
            project_id=job.project_id,
            job_id=job.id,
            event_type="job.intermediate_saved",
            payload={"job_id": job.id, "progress": job.progress},
        )
        session.commit()
        session.refresh(job)
        return job_to_read(job)

    failed_part_ids = request.failed_part_ids or _string_list(recovery.get("failed_parts"))
    if request.action == "RETRY_FAILED_PARTS" and not failed_part_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_FAILED_PARTS_MISSING",
                "message": "当前失败没有可单独重试的处理单元",
                "user_action": "改用从失败步骤继续或切换执行方案",
                "retryable": False,
                "details": {"job_id": job.id},
            },
        )

    input_payload = _json_object(job.input_json)
    input_payload["_recovery"] = {
        "action": request.action,
        "requested_at": now.isoformat(),
        "resume_from": recovery.get("failed_step"),
        "failed_part_ids": failed_part_ids,
        "model": request.model,
        "strategy": request.strategy,
        "additional_input": request.additional_input,
        "preserve_intermediate_output": True,
    }
    job.input_json = canonical_json(input_payload)
    job.status = "RETRY_WAIT"
    job.stage = {
        "RESUME_FROM_FAILURE": "等待从失败步骤继续",
        "RETRY_FAILED_PARTS": "等待重试失败部分",
        "SWITCH_MODEL": "等待切换模型或方案",
        "FALLBACK_EXECUTION": "等待降级执行",
        "PROVIDE_INPUT": "已补充信息，等待继续",
    }[request.action]
    job.available_at = now
    job.lease_until = None
    job.heartbeat_at = None
    job.worker_id = None
    job.cancel_requested = False
    job.completed_at = None
    job.error_code = None
    job.error_message = None
    job.error_details_json = canonical_json(
        {
            "recovery": {
                **recovery,
                "last_action": request.action,
                "last_requested_at": now.isoformat(),
            }
        }
    )
    job.retryable = True
    job.max_attempts = max(job.max_attempts, job.attempt + 1)
    job.updated_at = now
    mark_entity_job_running(session, job, now)
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="job.recovery_requested",
        payload={
            "job_id": job.id,
            "status": job.status,
            "action": request.action,
            "progress": job.progress,
        },
    )
    session.commit()
    session.refresh(job)
    return job_to_read(job)


def recover_expired_jobs(session: Session) -> int:
    now = datetime.now(UTC)
    expired = session.scalars(
        select(Job).where(
            Job.status.in_(ACTIVE_STATUSES),
            or_(Job.lease_until.is_(None), Job.lease_until < now),
        )
    ).all()
    for job in expired:
        if job.status == "CANCEL_REQUESTED" or job.cancel_requested:
            job.status = "CANCELLED"
            job.stage = "重启恢复时完成取消"
            job.completed_at = now
            event_type = "job.cancelled"
        elif job.retryable and job.attempt < job.max_attempts:
            job.status = "RETRY_WAIT"
            job.stage = "进程重启后等待恢复"
            job.available_at = now
            event_type = "job.recovered"
        else:
            job.status = "FAILED"
            job.stage = "恢复次数已耗尽"
            job.error_code = "WORKER_LEASE_EXPIRED"
            job.error_message = "任务执行期间后台任务进程心跳过期"
            job.completed_at = now
            event_type = "job.failed"
        if job.status in {"FAILED", "CANCELLED"}:
            release_entity_after_terminal_job(session, job, now)
        job.lease_until = None
        job.heartbeat_at = None
        job.worker_id = None
        job.updated_at = now
        append_event(
            session,
            project_id=job.project_id,
            job_id=job.id,
            event_type=event_type,
            payload={"job_id": job.id, "status": job.status, "attempt": job.attempt},
        )
    session.commit()
    return len(expired)


def claim_next_job(session: Session, worker_id: str, lease_seconds: int) -> Job | None:
    now = datetime.now(UTC)
    dependency_job = aliased(Job)
    unmet_dependency = exists(
        select(JobDependency.id)
        .join(dependency_job, dependency_job.id == JobDependency.depends_on_job_id)
        .where(
            JobDependency.job_id == Job.id,
            dependency_job.status != "SUCCEEDED",
        )
    )
    candidate = (
        select(Job.id)
        .where(
            Job.status.in_(QUEUED_STATUSES),
            Job.available_at <= now,
            or_(Job.lease_until.is_(None), Job.lease_until < now),
            ~unmet_dependency,
        )
        .order_by(Job.priority.desc(), Job.created_at.asc())
        .limit(1)
        .scalar_subquery()
    )
    claimed_id = session.scalar(
        update(Job)
        .where(
            Job.id == candidate,
            Job.status.in_(QUEUED_STATUSES),
            Job.available_at <= now,
        )
        .values(
            status="RUNNING",
            stage="任务已领取",
            attempt=Job.attempt + 1,
            worker_id=worker_id,
            heartbeat_at=now,
            lease_until=now + timedelta(seconds=lease_seconds),
            error_code=None,
            error_message=None,
            error_details_json=None,
            updated_at=now,
        )
        .returning(Job.id)
    )
    if claimed_id is None:
        session.rollback()
        return None
    job = session.get(Job, claimed_id)
    if job is None:
        session.rollback()
        return None
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="job.running",
        payload={"job_id": job.id, "status": "RUNNING", "attempt": job.attempt},
    )
    session.commit()
    session.refresh(job)
    return job


def update_job_progress(
    session: Session,
    *,
    job_id: str,
    worker_id: str,
    progress: float,
    stage: str,
    lease_seconds: int,
) -> bool:
    now = datetime.now(UTC)
    job = job_or_404(session, job_id)
    session.refresh(job)
    if job.cancel_requested or job.status == "CANCEL_REQUESTED":
        return False
    if job.status != "RUNNING" or job.worker_id != worker_id:
        return False
    next_progress = min(99, max(job.progress, progress))
    changed = job.progress != next_progress or job.stage != stage
    job.progress = next_progress
    job.stage = stage
    job.heartbeat_at = now
    job.lease_until = now + timedelta(seconds=lease_seconds)
    job.updated_at = now
    if changed:
        append_event(
            session,
            project_id=job.project_id,
            job_id=job.id,
            event_type="job.progress",
            payload={
                "job_id": job.id,
                "status": job.status,
                "progress": job.progress,
                "stage": stage,
            },
        )
    session.commit()
    return True


def update_job_diagnostics(
    session: Session,
    *,
    job_id: str,
    worker_id: str,
    details: dict[str, object],
    lease_seconds: int,
) -> bool:
    now = datetime.now(UTC)
    job = job_or_404(session, job_id)
    session.refresh(job)
    if job.cancel_requested or job.status == "CANCEL_REQUESTED":
        return False
    if job.status != "RUNNING" or job.worker_id != worker_id:
        return False
    job.error_details_json = canonical_json(details)
    job.heartbeat_at = now
    job.lease_until = now + timedelta(seconds=lease_seconds)
    job.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="job.diagnostics",
        payload={
            "job_id": job.id,
            "status": job.status,
            "phase": details.get("phase"),
            "model_attempt": details.get("model_attempt"),
        },
    )
    session.commit()
    return True


def update_job_intermediate_output(
    session: Session,
    *,
    job_id: str,
    worker_id: str,
    updates: dict[str, object],
    lease_seconds: int,
) -> bool:
    now = datetime.now(UTC)
    job = job_or_404(session, job_id)
    session.refresh(job)
    if job.cancel_requested or job.status == "CANCEL_REQUESTED":
        return False
    if job.status != "RUNNING" or job.worker_id != worker_id:
        return False
    output = _json_object(job.output_json)
    output.update(updates)
    job.output_json = canonical_json(output)
    job.heartbeat_at = now
    job.lease_until = now + timedelta(seconds=lease_seconds)
    job.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="job.intermediate_output",
        payload={
            "job_id": job.id,
            "status": job.status,
            "output_keys": sorted(output),
        },
    )
    session.commit()
    return True


def finish_job_success(
    session: Session, job_id: str, worker_id: str, output: dict[str, object]
) -> None:
    now = datetime.now(UTC)
    job = job_or_404(session, job_id)
    session.refresh(job)
    if job.worker_id != worker_id:
        return
    input_payload = _json_object(job.input_json)
    recovery_directive = input_payload.get("_recovery")
    recovery_action = (
        recovery_directive.get("action")
        if isinstance(recovery_directive, dict)
        else None
    )
    persisted_output = dict(output)
    recovery_details: dict[str, object] | None = None
    if isinstance(recovery_action, str):
        degraded = recovery_action == "FALLBACK_EXECUTION"
        persisted_output["recovery"] = {
            "action": recovery_action,
            "degraded": degraded,
        }
        recovery_details = {
            "recovery": {
                "completion_state": "DEGRADED_SUCCEEDED" if degraded else "RECOVERED",
                "last_action": recovery_action,
                "unreliable_outputs": (
                    ["降级执行生成的结果需要人工复核后才能作为最终交付"]
                    if degraded
                    else []
                ),
                "reliability_note": (
                    "任务已通过降级方案完成；可继续后续流程，但交付前必须人工复核。"
                    if degraded
                    else "任务已从失败点恢复并完成。"
                ),
            }
        }
    job.status = "SUCCEEDED"
    job.progress = 100
    job.stage = (
        "已降级完成"
        if recovery_action == "FALLBACK_EXECUTION"
        else "恢复后已完成"
        if isinstance(recovery_action, str)
        else "已完成"
    )
    job.output_json = canonical_json(persisted_output)
    job.error_code = None
    job.error_message = None
    job.error_details_json = (
        canonical_json(recovery_details) if recovery_details is not None else None
    )
    job.completed_at = now
    job.updated_at = now
    job.lease_until = None
    job.heartbeat_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="job.succeeded",
        payload={
            "job_id": job.id,
            "status": job.status,
            "progress": 100,
            "output": persisted_output,
        },
    )
    session.commit()


def finish_job_cancelled(session: Session, job_id: str, worker_id: str) -> None:
    now = datetime.now(UTC)
    job = job_or_404(session, job_id)
    session.refresh(job)
    if job.worker_id != worker_id:
        return
    job.status = "CANCELLED"
    job.stage = "已取消"
    job.completed_at = now
    job.updated_at = now
    job.lease_until = None
    release_entity_after_terminal_job(session, job, now)
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="job.cancelled",
        payload={"job_id": job.id, "status": job.status, "progress": job.progress},
    )
    session.commit()


def finish_job_failure(
    session: Session,
    *,
    job_id: str,
    worker_id: str,
    code: str,
    message: str,
    details: dict[str, object] | None,
    retryable: bool,
) -> None:
    now = datetime.now(UTC)
    job = job_or_404(session, job_id)
    session.refresh(job)
    if job.worker_id != worker_id:
        return
    can_retry = retryable and job.retryable and job.attempt < job.max_attempts
    if can_retry:
        delay = RETRY_DELAYS[min(max(job.attempt - 1, 0), len(RETRY_DELAYS) - 1)]
        job.status = "RETRY_WAIT"
        job.stage = f"{delay} 秒后重试"
        job.available_at = now + timedelta(seconds=delay)
        job.completed_at = None
        event_type = "job.retry_wait"
    else:
        job.status = "FAILED"
        job.stage = "任务失败"
        job.completed_at = now
        event_type = "job.failed"
        release_entity_after_terminal_job(session, job, now)
    job.error_code = code
    job.error_message = message
    job.error_details_json = canonical_json(_failure_details_with_recovery(job, details))
    job.updated_at = now
    job.lease_until = None
    job.worker_id = None
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type=event_type,
        payload={
            "job_id": job.id,
            "status": job.status,
            "attempt": job.attempt,
            "error_code": code,
            "retryable": can_retry,
        },
    )
    session.commit()


def upsert_worker_heartbeat(
    session: Session,
    *,
    state_id: str,
    worker_id: str,
    status: str,
    started_at: datetime,
    current_job_id: str | None,
) -> None:
    now = datetime.now(UTC)
    state = session.get(WorkerState, state_id)
    if state is None:
        state = WorkerState(
            id=state_id,
            worker_id=worker_id,
            status=status,
            started_at=started_at,
            heartbeat_at=now,
            current_job_id=current_job_id,
        )
        session.add(state)
    else:
        state.worker_id = worker_id
        state.status = status
        state.heartbeat_at = now
        state.current_job_id = current_job_id
    session.commit()
