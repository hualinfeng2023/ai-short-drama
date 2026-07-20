from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Job

Checkpoint = Callable[[Session, Job, float, str], Awaitable[None]]
RecordDiagnostics = Callable[[Session, Job, dict[str, object]], Awaitable[None]]
SaveIntermediateOutput = Callable[[Session, Job, dict[str, object]], Awaitable[None]]
Heartbeat = Callable[[Session, str, str | None], None]
AsyncProviderCall = Callable[..., Awaitable[Any]]


class JobCancelled(Exception):
    """Raised when a persisted cancellation is observed at a processing boundary."""


class JobExecutionError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


@dataclass(frozen=True)
class JobExecutionContext:
    settings: Settings
    worker_id: str
    checkpoint: Checkpoint
    record_diagnostics: RecordDiagnostics
    heartbeat: Heartbeat
    generate_image: AsyncProviderCall
    evaluate_identity_consistency: AsyncProviderCall
    generate_video: AsyncProviderCall
    cancel_video_task: AsyncProviderCall
    save_intermediate_output: SaveIntermediateOutput | None = None
