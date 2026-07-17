from collections.abc import Awaitable, Callable
from importlib import import_module

from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.contracts import JobExecutionContext

JobHandler = Callable[
    [JobExecutionContext, Session, Job, dict[str, object]],
    Awaitable[dict[str, object]],
]

_HANDLERS: dict[str, JobHandler] = {}
_LOADED = False
_HANDLER_MODULES = (
    "app.jobs.handlers.proposal",
    "app.jobs.handlers.production",
    "app.jobs.handlers.timeline",
    "app.jobs.handlers.demo",
    "app.jobs.handlers.image",
    "app.jobs.handlers.video",
    "app.jobs.handlers.audio",
    "app.jobs.handlers.multitrack",
    "app.jobs.handlers.delivery",
)


def register_job_handler(*job_types: str) -> Callable[[JobHandler], JobHandler]:
    if not job_types:
        raise ValueError("至少注册一个 Job Type")

    def decorator(handler: JobHandler) -> JobHandler:
        for job_type in job_types:
            if job_type in _HANDLERS:
                raise RuntimeError(f"Job Type 已注册：{job_type}")
            _HANDLERS[job_type] = handler
        return handler

    return decorator


def load_job_handlers() -> None:
    global _LOADED
    if _LOADED:
        return
    for module in _HANDLER_MODULES:
        import_module(module)
    _LOADED = True


def get_job_handler(job_type: str) -> JobHandler | None:
    load_job_handlers()
    return _HANDLERS.get(job_type)


def registered_job_types() -> frozenset[str]:
    load_job_handlers()
    return frozenset(_HANDLERS)
