"""SSE event stream backed by a durable outbox.

The worker and API are separate processes, so in-memory pub/sub cannot carry
worker progress to the browser.  ``publish`` remains the single call site used
by services, but now commits an ``event_outbox`` row when a database session is
available.  API SSE readers poll that table, which also makes
``Last-Event-ID`` replay possible after a reconnect.

Calling ``publish`` without a session keeps the old in-process behaviour for
unit tests and code paths that only report API-local state.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime

_subscribers: set[asyncio.Queue] = set()


def publish(event: str, data: dict, db=None) -> int | None:
    """Publish an event durably when ``db`` is supplied.

    Returns the outbox id on the durable path and ``None`` for the in-memory
    fallback.  Database failures must not turn a successful business operation
    into a failed one; callers that need durability can inspect the return value.
    """
    event_id = None
    if db is not None:
        from app.models import EventOutbox

        row = EventOutbox(event=event[:64], data=data, created_at=datetime.utcnow())
        db.add(row)
        # The caller is usually inside a business transaction.  Flush makes the
        # id available without committing; the event becomes visible only when
        # that transaction commits.
        db.flush()
        event_id = row.id
    return event_id


def events_since(db, after_id: int, *, limit: int = 200) -> list[tuple[int, str, dict]]:
    """Read committed events after an SSE cursor."""
    from sqlalchemy import select

    from app.models import EventOutbox

    rows = db.scalars(
        select(EventOutbox)
        .where(EventOutbox.id > after_id)
        .order_by(EventOutbox.id)
        .limit(limit)
    ).all()
    return [(row.id, row.event, row.data or {}) for row in rows]


async def subscribe(
    *,
    after_id: int = 0,
    poll_seconds: float = 1.0,
    session_factory=None,
) -> AsyncGenerator[str, None]:
    """SSE endpoint: replay outbox rows, then poll for new committed events."""
    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal

    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    cursor = max(0, after_id)
    try:
        yield _format("hello", {"ok": True, "last_event_id": cursor})
        while True:
            db = session_factory()
            try:
                rows = events_since(db, cursor)
            except Exception:
                # A transient database failure must not kill the browser's
                # stream; retry on the next poll after a keepalive.
                rows = []
            finally:
                db.close()
            if rows:
                for event_id, event, data in rows:
                    cursor = event_id
                    yield _format(event, data, event_id)
                continue
            # The in-memory queue is only a latency hint; the outbox above is
            # the source of truth.  Wait briefly for a notification, then loop
            # back to the database so events from another process are visible.
            try:
                await asyncio.wait_for(q.get(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        _subscribers.discard(q)


def _format(event: str, data: dict, event_id: int | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
