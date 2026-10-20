"""Keyword crawl command: HTTP only enqueues; the worker owns execution."""

from sqlalchemy.orm import Session

from app.core.queue import enqueue
from app.models import CrawlTask, JobQueue


class CrawlCommandError(ValueError):
    """The requested crawl cannot be enqueued."""


def enqueue_crawl(
    db: Session,
    task_id: int,
    *,
    priority: int = 100,
    commit: bool = True,
    dedup: bool = True,
) -> JobQueue:
    task = db.get(CrawlTask, task_id)
    if task is None:
        raise CrawlCommandError("采集任务不存在")
    return enqueue(
        db,
        "crawl",
        {"task_id": task.id},
        priority=priority,
        commit=commit,
        dedup=dedup,
    )
