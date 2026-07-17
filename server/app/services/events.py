import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import EventLog
from app.schemas import EventRead


def append_event(
    session: Session,
    *,
    project_id: str,
    event_type: str,
    payload: dict[str, object],
    job_id: str | None = None,
) -> EventLog:
    event = EventLog(
        event_id=str(uuid4()),
        project_id=project_id,
        job_id=job_id,
        event_type=event_type,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        created_at=datetime.now(UTC),
    )
    session.add(event)
    session.flush()
    return event


def event_to_read(event: EventLog) -> EventRead:
    return EventRead(
        sequence=event.sequence,
        event_id=event.event_id,
        project_id=event.project_id,
        job_id=event.job_id,
        event_type=event.event_type,
        payload=json.loads(event.payload_json),
        created_at=event.created_at,
    )


def list_events(
    session: Session, project_id: str, after_sequence: int = 0, limit: int = 100
) -> list[EventRead]:
    events = session.scalars(
        select(EventLog)
        .where(EventLog.project_id == project_id, EventLog.sequence > after_sequence)
        .order_by(EventLog.sequence)
        .limit(limit)
    ).all()
    return [event_to_read(event) for event in events]


def latest_event_sequence(session: Session, project_id: str) -> int:
    value = session.scalar(
        select(func.max(EventLog.sequence)).where(EventLog.project_id == project_id)
    )
    return int(value or 0)
