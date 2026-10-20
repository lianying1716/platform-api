"""Operational alerts: dedupe, acknowledgement and recovery."""

import asyncio

from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import Alert, EventOutbox, JobQueue
from app.services import alerts


def _job(session, *, status="error", job_type="download"):
    row = JobQueue(job_type=job_type, payload={"asset_id": 1}, status=status, error="boom")
    session.add(row)
    session.commit()
    return row


def test_failure_alert_dedupes_same_job(session):
    job = _job(session)
    first = alerts.raise_job_failure(session, job, message="首次失败")
    session.commit()
    second = alerts.raise_job_failure(session, job, message="再次失败")
    session.commit()
    assert first.id == second.id
    assert second.occurrences == 2
    assert second.status == "active"
    assert session.scalars(select(Alert)).all() == [second]
    assert len(session.scalars(select(EventOutbox)).all()) == 2


def test_acknowledged_alert_reopens_on_new_occurrence(session):
    job = _job(session)
    row = alerts.raise_job_failure(session, job, message="失败")
    session.commit()
    assert alerts.acknowledge(session, row.id).status == "acknowledged"
    assert alerts.active_count(session) == 1
    reopened = alerts.raise_job_failure(session, job, message="再次失败")
    session.commit()
    assert reopened.status == "active"
    assert reopened.acknowledged_at is None


def test_success_resolves_alerts(session):
    job = _job(session)
    row = alerts.raise_job_failure(session, job, message="失败")
    session.commit()
    assert alerts.resolve_job_alerts(session, job.id) == 1
    session.commit()
    session.refresh(row)
    assert row.status == "resolved" and row.resolved_at is not None
    assert alerts.active_count(session) == 0


def test_alert_api_acknowledge_and_resolve(client, session):
    job = _job(session)
    row = alerts.raise_job_failure(session, job, message="失败")
    session.commit()
    body = client.get("/api/pipeline/alerts").json()
    assert body["active"] == 1 and body["alerts"][0]["id"] == row.id
    assert client.post(f"/api/pipeline/alerts/{row.id}/acknowledge").json()["status"] == "acknowledged"
    assert client.post(f"/api/pipeline/alerts/{row.id}/resolve").json()["status"] == "resolved"
    assert client.get("/api/pipeline/alerts").json()["alerts"] == []


def test_stale_job_creates_one_alert(session):
    from app.core import queue

    job = JobQueue(
        job_type="download",
        payload={"asset_id": 1},
        status="running",
        locked_at=datetime.utcnow() - timedelta(hours=2),
        heartbeat_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(job)
    session.commit()
    assert queue.reset_stale_running(session, older_than_minutes=30) == 1
    assert session.scalars(select(Alert)).one().kind == "job_stale"
    assert queue.reset_stale_running(session, older_than_minutes=30) == 0
    assert session.scalars(select(Alert)).one().occurrences == 1


def test_worker_failure_creates_alert_and_success_resolves_it(_engine, monkeypatch):
    import app.workers.runner as runner
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    job = JobQueue(job_type="download", payload={"asset_id": 5}, max_attempts=1)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    async def boom(db, payload):
        raise RuntimeError("worker boom")

    monkeypatch.setattr(runner, "SessionLocal", Session)
    monkeypatch.setattr(runner, "HANDLERS", {"download": boom})
    assert asyncio.run(runner._run_one()) is True
    db = Session()
    alert = db.scalars(select(Alert)).one()
    assert alert.status == "active" and alert.job_id == job_id
    db.close()
