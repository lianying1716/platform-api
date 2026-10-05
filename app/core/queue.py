"""基于 PostgreSQL 的任务队列。

取任务用 SELECT ... FOR UPDATE SKIP LOCKED，多 worker 并发安全，不引入 Redis/Celery。
任务表见 models.JobQueue。job_type: download / transcribe / analyze / sync / crawl。
"""

from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import JobQueue


class NonRetryableJobError(Exception):
    """不可重试的任务失败（输入/数据问题）。worker 直接置 error，不重新排队。"""


def enqueue(
    db: Session,
    job_type: str,
    payload: dict,
    *,
    priority: int = 100,
    max_attempts: int = 3,
    run_after: datetime | None = None,
    dedup: bool = True,
    commit: bool = True,
) -> JobQueue:
    """投递一个任务。dedup=True 时，相同 job_type+payload 的未完成任务不重复投递。

    commit=False 时只 flush 不提交，便于调用方在一个事务里批量投递后统一 commit。
    """
    if dedup:
        existing = db.scalar(
            select(JobQueue).where(
                JobQueue.job_type == job_type,
                JobQueue.status.in_(("queued", "running")),
                JobQueue.payload == payload,
            )
        )
        if existing is not None:
            return existing
    job = JobQueue(
        job_type=job_type,
        payload=payload,
        priority=priority,
        max_attempts=max_attempts,
        run_after=run_after or datetime.utcnow(),
    )
    db.add(job)
    if commit:
        db.commit()
        db.refresh(job)
    else:
        db.flush()
    return job


def claim(db: Session, job_types: list[str] | None = None) -> JobQueue | None:
    """原子领取一个待执行任务并标记 running。无任务时返回 None。

    用 FOR UPDATE SKIP LOCKED 避免多 worker 抢同一行。SQLite 不支持该语法，
    测试环境降级为普通查询（单线程，无并发竞争）。
    """
    is_sqlite = db.bind.dialect.name == "sqlite"
    stmt = (
        select(JobQueue)
        .where(
            JobQueue.status == "queued",
            JobQueue.run_after <= datetime.utcnow(),
        )
        .order_by(JobQueue.priority, JobQueue.id)
        .limit(1)
    )
    if job_types:
        stmt = stmt.where(JobQueue.job_type.in_(job_types))
    if not is_sqlite:
        stmt = stmt.with_for_update(skip_locked=True)

    job = db.scalar(stmt)
    if job is None:
        return None
    job.status = "running"
    job.locked_at = datetime.utcnow()
    job.attempts += 1
    db.commit()
    db.refresh(job)
    return job


def complete(db: Session, job: JobQueue) -> None:
    job.status = "done"
    job.error = ""
    db.commit()


def fail(
    db: Session,
    job: JobQueue,
    error: str,
    *,
    retry_delay_seconds: int = 60,
    retry: bool = True,
) -> None:
    """失败处理：未超重试上限则重新排队（延后 run_after），否则置 error。

    `retry=False` 表示不可重试的失败（如输入/数据问题），直接置 error，不占用重试额度。
    """
    job.error = error[:2000]
    if not retry or job.attempts >= job.max_attempts:
        job.status = "error"
    else:
        job.status = "queued"
        job.locked_at = None
        job.run_after = datetime.utcnow() + timedelta(seconds=retry_delay_seconds)
    db.commit()


def reset_stale_running(db: Session, *, older_than_minutes: int = 30) -> int:
    """把卡在 running 太久（worker 崩溃遗留）的任务重新排队。返回重置条数。"""
    cutoff = datetime.utcnow() - timedelta(minutes=older_than_minutes)
    rows = db.scalars(
        select(JobQueue).where(JobQueue.status == "running", JobQueue.locked_at < cutoff)
    ).all()
    for job in rows:
        job.status = "queued"
        job.locked_at = None
    db.commit()
    return len(rows)


def retry(db: Session, job_id: int) -> JobQueue | None:
    """手动重试一个 error 任务（处理队列页用）。"""
    job = db.get(JobQueue, job_id)
    if job is None or job.status != "error":
        return None
    job.status = "queued"
    job.attempts = 0
    job.locked_at = None
    job.error = ""
    job.run_after = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job
