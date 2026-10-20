"""Reconcile domain runs and probe rows left behind by crashed workers.

A ``running`` domain record must never outlive the job that owns it. The normal
path closes runs inside the service; this module is the crash/timeout backstop.
Only records with no live queue job are failed, so a slow but healthy job is not
stolen by the janitor.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AiModel, CrawlRun, JobQueue, SyncRun

_STALE_MESSAGE = "运行超时（worker 异常或重启），可重新触发"


def _live_job_ids(db: Session, job_type: str, cutoff: datetime) -> set[int]:
    """Jobs still healthy enough to own a ``running`` run.

    A queued job means the previous attempt is over (the worker will create a new
    run on its next claim), so it must not keep the old run alive. A running job is
    live only while its heartbeat is fresh.
    """
    rows = db.scalars(
        select(JobQueue.id).where(
            JobQueue.job_type == job_type,
            JobQueue.status == "running",
            func.coalesce(JobQueue.heartbeat_at, JobQueue.locked_at, JobQueue.updated_at) >= cutoff,
        )
    ).all()
    return {int(x) for x in rows}


def _stale_cutoff(minutes: int) -> datetime:
    return datetime.utcnow() - timedelta(minutes=max(1, minutes))


def reconcile_runs(db: Session, *, older_than_minutes: int = 30) -> dict[str, int]:
    """Fail stale SyncRun/CrawlRun rows whose job is gone or already terminal."""
    cutoff = _stale_cutoff(older_than_minutes)
    out: dict[str, int] = {}

    live_sync = _live_job_ids(db, "sync", cutoff)
    sync_rows = db.scalars(
        select(SyncRun).where(
            SyncRun.status == "running",
            func.coalesce(SyncRun.heartbeat_at, SyncRun.started_at) < cutoff,
        )
    ).all()
    fixed = 0
    for run in sync_rows:
        if run.job_id is not None and run.job_id in live_sync:
            continue
        run.status = "error"
        run.error = _STALE_MESSAGE
        run.finished_at = datetime.utcnow()
        run.heartbeat_at = None
        fixed += 1
    if fixed:
        out["sync"] = fixed

    live_crawl = _live_job_ids(db, "crawl", cutoff)
    crawl_rows = db.scalars(
        select(CrawlRun).where(
            CrawlRun.status == "running",
            func.coalesce(CrawlRun.heartbeat_at, CrawlRun.started_at) < cutoff,
        )
    ).all()
    fixed = 0
    for run in crawl_rows:
        if run.job_id is not None and run.job_id in live_crawl:
            continue
        run.status = "error"
        run.error = _STALE_MESSAGE
        run.finished_at = datetime.utcnow()
        run.heartbeat_at = None
        fixed += 1
    if fixed:
        out["crawl"] = fixed

    live_probe_models: set[int] = set()
    for job in db.scalars(
        select(JobQueue).where(
            JobQueue.job_type == "probe_model",
            JobQueue.status.in_(("queued", "running")),
            func.coalesce(
                JobQueue.heartbeat_at, JobQueue.locked_at, JobQueue.updated_at, JobQueue.created_at
            ) >= cutoff,
        )
    ).all():
        try:
            live_probe_models.add(int((job.payload or {}).get("model_id")))
        except (TypeError, ValueError):
            continue

    probe_rows = db.scalars(
        select(AiModel).where(
            AiModel.probe_status == "testing",
            func.coalesce(AiModel.probe_started_at, AiModel.updated_at) < cutoff,
        )
    ).all()
    fixed = 0
    for model in probe_rows:
        if model.id in live_probe_models:
            continue
        model.probe_status = "failed"
        if not (model.probe_error or "").strip():
            model.probe_error = _STALE_MESSAGE
        model.probe_started_at = None
        fixed += 1
    if fixed:
        out["probe"] = fixed

    if out:
        db.commit()
    return out


def reconcile_terminal_run(
    db: Session, job_type: str, payload: dict, *, job_id: int | None
) -> bool:
    """Close a domain run immediately when its owning job already ended in error."""
    if job_id is None:
        return False
    run: SyncRun | CrawlRun | None
    if job_type == "sync":
        run = db.scalar(select(SyncRun).where(SyncRun.job_id == job_id, SyncRun.status == "running"))
    elif job_type == "crawl":
        run = db.scalar(select(CrawlRun).where(CrawlRun.job_id == job_id, CrawlRun.status == "running"))
    else:
        return False
    if run is None:
        return False
    run.status = "error"
    if not (run.error or "").strip():
        run.error = _STALE_MESSAGE
    run.finished_at = datetime.utcnow()
    run.heartbeat_at = None
    db.commit()
    return True
