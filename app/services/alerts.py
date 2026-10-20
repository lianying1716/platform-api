"""Operational alerts: dedupe, acknowledge and recover job failures."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import events
from app.models import Alert, JobQueue


def _upsert(
    db: Session,
    *,
    kind: str,
    dedup_key: str,
    title: str,
    message: str,
    job: JobQueue,
    severity: str = "error",
) -> Alert:
    now = datetime.utcnow()
    row = db.scalar(select(Alert).where(Alert.dedup_key == dedup_key))
    if row is None:
        row = Alert(
            kind=kind,
            dedup_key=dedup_key,
            severity=severity,
            status="active",
            title=title[:160],
            message=message[:512],
            job_id=job.id,
            job_type=job.job_type,
            occurrences=1,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(row)
        db.flush()
    else:
        row.kind = kind
        row.severity = severity
        # A new occurrence reopens a dismissed alert.  This is intentional:
        # acknowledging is not a permanent mute for a recurring failure.
        row.status = "active"
        row.title = title[:160]
        row.message = message[:512]
        row.job_id = job.id
        row.job_type = job.job_type
        row.occurrences = int(row.occurrences or 0) + 1
        row.last_seen_at = now
        row.acknowledged_at = None
        row.resolved_at = None
    events.publish(
        "job_alert",
        {
            "alert_id": row.id,
            "kind": kind,
            "status": row.status,
            "job_id": job.id,
            "job_type": job.job_type,
            "title": row.title,
        },
        db=db,
    )
    return row


def raise_job_failure(db: Session, job: JobQueue, *, message: str) -> Alert:
    return _upsert(
        db,
        kind="job_failed",
        dedup_key=f"job-failed:{job.id}",
        title=f"{job.job_type} 任务失败",
        message=message or job.error or "任务失败，可重试",
        job=job,
    )


def raise_job_stale(db: Session, job: JobQueue) -> Alert:
    return _upsert(
        db,
        kind="job_stale",
        dedup_key=f"job-stale:{job.id}",
        title=f"{job.job_type} 任务卡死",
        message="任务心跳超时，worker 可能已中断；已重新排队，请关注重试结果",
        job=job,
    )


def resolve_job_alerts(db: Session, job_id: int) -> int:
    rows = db.scalars(
        select(Alert).where(
            Alert.job_id == job_id,
            Alert.status.in_(("active", "acknowledged")),
        )
    ).all()
    now = datetime.utcnow()
    for row in rows:
        row.status = "resolved"
        row.resolved_at = now
        events.publish(
            "job_alert",
            {"alert_id": row.id, "kind": row.kind, "status": "resolved", "job_id": job_id},
            db=db,
        )
    return len(rows)


def acknowledge(db: Session, alert_id: int) -> Alert | None:
    row = db.get(Alert, alert_id)
    if row is None or row.status not in ("active", "acknowledged"):
        return None
    row.status = "acknowledged"
    row.acknowledged_at = datetime.utcnow()
    events.publish("job_alert", {"alert_id": row.id, "status": row.status}, db=db)
    db.commit()
    db.refresh(row)
    return row


def resolve(db: Session, alert_id: int) -> Alert | None:
    row = db.get(Alert, alert_id)
    if row is None or row.status == "resolved":
        return None
    row.status = "resolved"
    row.resolved_at = datetime.utcnow()
    events.publish("job_alert", {"alert_id": row.id, "status": row.status}, db=db)
    db.commit()
    db.refresh(row)
    return row


def list_alerts(db: Session, *, status: str | None = None, limit: int = 100) -> list[Alert]:
    stmt = select(Alert).order_by(Alert.last_seen_at.desc(), Alert.id.desc()).limit(max(1, min(limit, 300)))
    if status:
        stmt = stmt.where(Alert.status == status)
    else:
        stmt = stmt.where(Alert.status.in_(("active", "acknowledged")))
    return list(db.scalars(stmt).all())


def active_count(db: Session) -> int:
    from sqlalchemy import func

    # Acknowledged alerts are still unresolved and must remain visible until
    # they are closed or the owning job succeeds.
    return int(
        db.scalar(select(func.count(Alert.id)).where(Alert.status.in_(("active", "acknowledged")))) or 0
    )
