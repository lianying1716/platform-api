"""Subscription sync command: HTTP only enqueues; the worker owns execution."""

from sqlalchemy.orm import Session

from app.core.queue import enqueue
from app.models import JobQueue, Subscription


class SyncCommandError(ValueError):
    """The requested sync cannot be enqueued."""


def enqueue_sync(
    db: Session,
    subscription_id: int,
    *,
    force: bool = False,
    priority: int = 100,
) -> JobQueue:
    sub = db.get(Subscription, subscription_id)
    if sub is None or sub.deleted_at is not None:
        raise SyncCommandError("订阅不存在")
    return enqueue(
        db,
        "sync",
        {"subscription_id": sub.id, "force": bool(force)},
        priority=priority,
    )
