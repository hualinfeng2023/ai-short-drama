import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import Job
from app.db.session import get_engine
from app.jobs.contracts import JobCancelled, JobExecutionContext, JobExecutionError
from app.jobs.registry import get_job_handler
from app.services.identity_consistency import evaluate_identity_consistency
from app.services.image_provider import generate_image
from app.services.jobs import (
    claim_next_job,
    finish_job_cancelled,
    finish_job_failure,
    finish_job_success,
    reconcile_terminal_project_jobs,
    recover_expired_jobs,
    update_job_diagnostics,
    update_job_progress,
    upsert_worker_heartbeat,
)
from app.services.video_provider import cancel_video_task, generate_video

WORKER_STATE_ID = "70000000-0000-4000-8000-000000000001"
logger = logging.getLogger(__name__)


class PersistentJobWorker:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.worker_id = f"worker-{uuid4()}"
        self.started_at = datetime.now(UTC)
        self._factory = sessionmaker(
            bind=get_engine(self.settings.database_url),
            expire_on_commit=False,
        )
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        with self._factory() as session:
            recover_expired_jobs(session)
            reconcile_terminal_project_jobs(session)
            self._heartbeat(session, "IDLE", None)
        self._task = asyncio.create_task(self._run(), name="persistent-job-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
        with self._factory() as session:
            self._heartbeat(session, "STOPPED", None)

    def _heartbeat(self, session: Session, status: str, current_job_id: str | None) -> None:
        upsert_worker_heartbeat(
            session,
            state_id=WORKER_STATE_ID,
            worker_id=self.worker_id,
            status=status,
            started_at=self.started_at,
            current_job_id=current_job_id,
        )

    def _job_heartbeat_interval(self) -> float:
        return max(
            0.1,
            min(
                self.settings.worker_heartbeat_stale_seconds / 2,
                self.settings.worker_lease_seconds / 3,
            ),
        )

    async def _job_heartbeat_loop(self, job_id: str) -> None:
        interval = self._job_heartbeat_interval()
        while True:
            await asyncio.sleep(interval)
            try:
                # A dedicated session keeps Worker liveness independent from a
                # handler that is awaiting a slow provider response.
                with self._factory() as session:
                    self._heartbeat(session, "RUNNING", job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("刷新后台任务 Worker 心跳失败；下个周期将重试")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                handled = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("后台任务循环异常；Worker 将继续运行并等待租约恢复")
                with suppress(Exception):
                    with self._factory() as session:
                        self._heartbeat(session, "DEGRADED", None)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.settings.worker_poll_interval,
                    )
                except TimeoutError:
                    pass
                continue
            if not handled:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.settings.worker_poll_interval,
                    )
                except TimeoutError:
                    pass

    async def run_once(self) -> bool:
        with self._factory() as session:
            recover_expired_jobs(session)
            self._heartbeat(session, "IDLE", None)
            job = claim_next_job(session, self.worker_id, self.settings.worker_lease_seconds)
            if job is None:
                return False
            self._heartbeat(session, "RUNNING", job.id)
            heartbeat_task = asyncio.create_task(
                self._job_heartbeat_loop(job.id),
                name=f"job-worker-heartbeat-{job.id}",
            )
            try:
                output = await self._execute(session, job)
                finish_job_success(session, job.id, self.worker_id, output)
            except JobCancelled:
                session.rollback()
                finish_job_cancelled(session, job.id, self.worker_id)
            except JobExecutionError as exc:
                session.rollback()
                finish_job_failure(
                    session,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                    retryable=exc.retryable,
                )
            except Exception as exc:  # worker must persist unexpected failures and continue
                session.rollback()
                finish_job_failure(
                    session,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    code="UNEXPECTED_JOB_ERROR",
                    message=str(exc),
                    details={"exception_type": type(exc).__name__},
                    retryable=True,
                )
            finally:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
                with suppress(Exception):
                    session.rollback()
                    self._heartbeat(session, "IDLE", None)
            return True

    async def _checkpoint(
        self,
        session: Session,
        job: Job,
        progress: float,
        stage: str,
    ) -> None:
        active = update_job_progress(
            session,
            job_id=job.id,
            worker_id=self.worker_id,
            progress=progress,
            stage=stage,
            lease_seconds=self.settings.worker_lease_seconds,
        )
        if not active:
            raise JobCancelled
        await asyncio.sleep(0.05)

    async def _record_diagnostics(
        self,
        session: Session,
        job: Job,
        details: dict[str, object],
    ) -> None:
        active = update_job_diagnostics(
            session,
            job_id=job.id,
            worker_id=self.worker_id,
            details=details,
            lease_seconds=self.settings.worker_lease_seconds,
        )
        if not active:
            raise JobCancelled
        await asyncio.sleep(0.05)

    async def _execute(self, session: Session, job: Job) -> dict[str, object]:
        handler = get_job_handler(job.job_type)
        if handler is None:
            raise JobExecutionError(
                "JOB_HANDLER_NOT_FOUND",
                f"没有为任务类型 {job.job_type} 注册处理器",
                retryable=False,
            )
        payload = json.loads(job.input_json)
        if not isinstance(payload, dict):
            raise JobExecutionError(
                "JOB_INPUT_INVALID",
                "任务输入必须是 JSON Object",
                retryable=False,
            )
        recovery = payload.get("_recovery")
        if isinstance(recovery, dict):
            action = recovery.get("action")
            payload["recovery_action"] = action
            payload["resume_from"] = recovery.get("resume_from")
            if action == "RETRY_FAILED_PARTS":
                payload["target_part_ids"] = recovery.get("failed_part_ids", [])
            elif action == "SWITCH_MODEL":
                if recovery.get("model"):
                    payload["model"] = recovery["model"]
                payload["execution_strategy"] = recovery.get("strategy", "auto-alternate")
            elif action == "FALLBACK_EXECUTION":
                payload["execution_mode"] = "DEGRADED"
                payload["allow_degraded"] = True
                payload["execution_strategy"] = recovery.get("strategy", "stability-first")
            elif action == "PROVIDE_INPUT":
                payload["additional_context"] = recovery.get("additional_input")
        context = JobExecutionContext(
            settings=self.settings,
            worker_id=self.worker_id,
            checkpoint=self._checkpoint,
            record_diagnostics=self._record_diagnostics,
            heartbeat=self._heartbeat,
            # These module globals intentionally preserve the existing monkeypatch contract
            # used by provider integration tests and local manual smoke runs.
            generate_image=generate_image,
            evaluate_identity_consistency=evaluate_identity_consistency,
            generate_video=generate_video,
            cancel_video_task=cancel_video_task,
        )
        return await handler(context, session, job, payload)
