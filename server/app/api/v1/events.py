import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import sessionmaker

from app.api.trace import success
from app.config import get_settings
from app.db.session import get_engine
from app.services.events import latest_event_sequence, list_events
from app.services.workspace import project_or_404

router = APIRouter(prefix="/api/v1", tags=["events"])


def _cursor(after: int, last_event_id: str | None) -> int:
    if last_event_id is None:
        return after
    try:
        return max(after, int(last_event_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_EVENT_CURSOR",
                "message": "Last-Event-ID 必须是事件序号",
                "user_action": "清除旧游标并重新连接",
                "retryable": False,
                "details": {"last_event_id": last_event_id},
            },
        ) from exc


async def _event_stream(
    request: Request, project_id: str, initial_cursor: int
) -> AsyncIterator[str]:
    settings = get_settings()
    factory = sessionmaker(bind=get_engine(settings.database_url), expire_on_commit=False)
    cursor = initial_cursor
    idle_ticks = 0
    while not await request.is_disconnected():
        with factory() as session:
            events = list_events(session, project_id, cursor, 100)
        if events:
            idle_ticks = 0
            for event in events:
                cursor = event.sequence
                payload = json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {payload}\n\n"
        else:
            idle_ticks += 1
            if idle_ticks >= 10:
                idle_ticks = 0
                yield ": keepalive\n\n"
        await asyncio.sleep(0.3)


@router.get("/projects/{project_id}/events")
def project_events(
    request: Request,
    project_id: str,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):  # noqa: ANN201
    settings = get_settings()
    factory = sessionmaker(bind=get_engine(settings.database_url), expire_on_commit=False)
    cursor = _cursor(after, last_event_id)
    accepts_stream = "text/event-stream" in request.headers.get("accept", "")
    # Do not inject a request-scoped Session here. A dependency that yields a
    # Session is only closed after StreamingResponse completes, so every open
    # SSE tab would retain a pool connection for the lifetime of the stream.
    with factory() as session:
        project_or_404(session, project_id)
        if not accepts_stream:
            return success(list_events(session, project_id, cursor, 100))
        if last_event_id is None and after == 0:
            # Page loads already fetch a current snapshot. Starting a fresh SSE
            # connection at the latest sequence prevents every historical event
            # from triggering a concurrent workspace refresh.
            cursor = latest_event_sequence(session, project_id)
    return StreamingResponse(
        _event_stream(request, project_id, cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
