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
        # 不能用 `JobQueue.payload == payload` 做等值比较：PG 上 JSON 列映射为 `json`
        # 类型，而 `json` 没有 `=` 运算符（只有 `jsonb` 有），该比较会抛
        # `operator does not exist: json = json`（SQLite 的 JSON 支持 `=`，会掩盖此差异）。
        # 改为先取同类型的未完成任务，再在 Python 端比较 payload。
        rows = db.scalars(
            select(JobQueue).where(
                JobQueue.job_type == job_type,
                JobQueue.status.in_(("queued", "running")),
            )
        ).all()
        for existing in rows:
            if (existing.payload or {}) == payload:
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


def claim(
    db: Session,
    job_types: list[str] | None = None,
    *,
    schedule: dict | None = None,
) -> JobQueue | None:
    """原子领取一个待执行任务并标记 running。无任务时返回 None。

    用 FOR UPDATE SKIP LOCKED 避免多 worker 抢同一行。SQLite 不支持该语法，
    测试环境降级为普通查询（单线程，无并发竞争）。

    AI 时段关闭时，调用大模型的任务留在 queued，其它任务照常领取。任务在领取
    前被门控，不会出现跑到一半被中断。
    """
    from app.core.ai_schedule import AI_JOB_TYPES, get_ai_schedule, is_ai_allowed

    schedule = schedule if schedule is not None else get_ai_schedule(db)
    blocked_types: frozenset[str] = frozenset()
    if not is_ai_allowed(schedule):
        blocked_types = AI_JOB_TYPES
        if job_types and set(job_types) <= blocked_types:
            return None

    if job_types:
        effective_types = [t for t in job_types if t not in blocked_types]
        if not effective_types:
            return None
    else:
        effective_types = None

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
    if effective_types:
        stmt = stmt.where(JobQueue.job_type.in_(effective_types))
    elif blocked_types:
        stmt = stmt.where(JobQueue.job_type.notin_(tuple(blocked_types)))
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
