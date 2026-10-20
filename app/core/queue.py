"""基于 PostgreSQL 的任务队列。

取任务用 SELECT ... FOR UPDATE SKIP LOCKED，多 worker 并发安全，不引入 Redis/Celery。
任务表见 models.JobQueue。job_type: download / transcribe / analyze / sync / crawl。
"""

import hashlib
import json
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import JobQueue


class NonRetryableJobError(Exception):
    """不可重试的任务失败（输入/数据问题）。worker 直接置 error，不重新排队。"""


def dedup_key(job_type: str, payload: dict | None) -> str:
    """Stable identity for an active job.

    JSON object ordering and whitespace are irrelevant to command identity, so
    canonicalize both before hashing.  The value is intentionally opaque to
    callers; tests may inspect it, but dedup semantics remain payload-based.
    """
    canonical = json.dumps(
        {"job_type": job_type, "payload": payload or {}},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    key = dedup_key(job_type, payload) if dedup else None
    if key is not None:
        existing = db.scalar(
            select(JobQueue).where(
                JobQueue.dedup_key == key,
                JobQueue.status.in_(("queued", "running")),
            )
        )
        if existing is not None:
            return existing
    job = JobQueue(
        job_type=job_type,
        payload=payload,
        dedup_key=key,
        priority=priority,
        max_attempts=max_attempts,
        run_after=run_after or datetime.utcnow(),
    )
    values = {
        "job_type": job_type,
        "payload": payload,
        "dedup_key": key,
        "status": "queued",
        "priority": priority,
        "attempts": 0,
        "max_attempts": max_attempts,
        "error": "",
        "run_after": job.run_after,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    dialect = db.get_bind().dialect.name
    index = next(
        idx for idx in JobQueue.__table__.indexes if idx.name == "uq_job_queue_active_dedup_key"
    )
    if key is not None and dialect == "postgresql":
        stmt = pg_insert(JobQueue).values(**values).on_conflict_do_nothing(
            index_elements=["dedup_key"],
            index_where=index.dialect_options["postgresql"]["where"],
        )
    elif key is not None and dialect == "sqlite":
        stmt = sqlite_insert(JobQueue).values(**values).on_conflict_do_nothing(
            index_elements=["dedup_key"],
            index_where=index.dialect_options["sqlite"]["where"],
        )
    else:
        stmt = None
    if stmt is not None:
        result = db.execute(stmt)
        if result.rowcount == 0:
            existing = db.scalar(
                select(JobQueue).where(
                    JobQueue.dedup_key == key,
                    JobQueue.status.in_(("queued", "running")),
                )
            )
            if existing is None:
                raise RuntimeError("enqueue conflict without an active row")
            if commit:
                db.commit()
            return existing
        inserted_id = result.inserted_primary_key[0]
        job = db.get(JobQueue, inserted_id)
    else:
        db.add(job)
        db.flush()
    if commit:
        db.commit()
        db.refresh(job)
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
    job.heartbeat_at = job.locked_at
    job.attempts += 1
    db.commit()
    db.refresh(job)
    return job


def complete(db: Session, job: JobQueue) -> None:
    job.status = "done"
    job.error = ""
    job.heartbeat_at = None
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
        job.dedup_key = None
        job.heartbeat_at = None
    else:
        job.status = "queued"
        job.locked_at = None
        job.heartbeat_at = None
        job.run_after = datetime.utcnow() + timedelta(seconds=retry_delay_seconds)
    db.commit()


def heartbeat(db: Session, job_id: int, *, now: datetime | None = None) -> bool:
    """刷新运行中任务的心跳；不是 running 时返回 False。"""
    job = db.get(JobQueue, job_id)
    if job is None or job.status != "running":
        return False
    job.heartbeat_at = now or datetime.utcnow()
    db.commit()
    return True


def reset_stale_running(db: Session, *, older_than_minutes: int = 30) -> int:
    """把心跳过期的 running 任务重新排队。返回重置条数。

    只按 ``locked_at`` 判断会把仍在执行的长任务误回收；优先使用 worker 周期刷新的
    ``heartbeat_at``，存量行没有心跳时才退回领取时间。领域运行记录由 run_reconciler
    同步对账，重置任务本身不越界修改业务状态。
    """
    cutoff = datetime.utcnow() - timedelta(minutes=older_than_minutes)
    rows = db.scalars(
        select(JobQueue).where(
            JobQueue.status == "running",
            func.coalesce(JobQueue.heartbeat_at, JobQueue.locked_at, JobQueue.updated_at) < cutoff,
        )
    ).all()
    if rows:
        from app.services import alerts

        for job in rows:
            alerts.raise_job_stale(db, job)
    for job in rows:
        job.status = "queued"
        job.locked_at = None
        job.heartbeat_at = None
    db.commit()
    return len(rows)


def retry(db: Session, job_id: int, *, commit: bool = True) -> JobQueue | None:
    """手动重试一个 error 任务（处理队列页用）。"""
    job = db.get(JobQueue, job_id)
    if job is None or job.status != "error":
        return None
    job.status = "queued"
    job.dedup_key = dedup_key(job.job_type, job.payload or {})
    job.attempts = 0
    job.locked_at = None
    job.heartbeat_at = None
    job.error = ""
    job.run_after = datetime.utcnow()
    bind = db.connection()
    savepoint = bind.begin_nested()
    try:
        db.flush()
        savepoint.commit()
    except IntegrityError:
        # The same command is already queued/running.  Returning the failed
        # duplicate would misreport it as retryable; leave it in error.
        savepoint.rollback()
        return None
    if commit:
        db.commit()
        db.refresh(job)
    return job


def retry_failed(
    db: Session,
    *,
    job_type: str | None = None,
    batch_size: int = 500,
) -> list[int]:
    """Retry all failed jobs in bounded transactions.

    A single transaction for an unbounded failure set can hold locks for too
    long.  Each batch is one commit, preserving atomicity within the batch
    while bounding the transaction size.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    retried: list[int] = []
    last_id = 0
    while True:
        stmt = (
            select(JobQueue)
            .where(JobQueue.status == "error", JobQueue.id > last_id)
            .order_by(JobQueue.id)
            .limit(batch_size)
        )
        if job_type:
            stmt = stmt.where(JobQueue.job_type == job_type)
        jobs = db.scalars(stmt).all()
        if not jobs:
            break
        keys = {job.id: dedup_key(job.job_type, job.payload or {}) for job in jobs}
        occupied = set(
            db.scalars(
                select(JobQueue.dedup_key).where(
                    JobQueue.dedup_key.in_(keys.values()),
                    JobQueue.status.in_(("queued", "running")),
                )
            ).all()
        )
        for job in jobs:
            last_id = job.id
            key = keys[job.id]
            if key in occupied:
                continue
            occupied.add(key)
            job.status = "queued"
            job.attempts = 0
            job.locked_at = None
            job.heartbeat_at = None
            job.error = ""
            job.run_after = datetime.utcnow()
            job.dedup_key = key
            retried.append(job.id)
        db.commit()
    return retried
