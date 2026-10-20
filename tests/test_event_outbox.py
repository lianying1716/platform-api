"""Durable event outbox and TaskLog query contracts for OBS-01."""

import asyncio

from sqlalchemy import select

from app.core import events
from app.models import EventOutbox, JobQueue, TaskLog
from app.services import foundation


def test_publish_commits_with_business_transaction(session):
    job = JobQueue(job_type="download", payload={"asset_id": 1})
    session.add(job)
    session.flush()
    foundation.log_task(session, job.id, "download", "error", "下载失败", detail={"code": "network"})
    event_id = events.publish(
        "asset_status",
        {"asset_id": 1, "download": "error"},
        db=session,
    )
    session.commit()

    assert event_id is not None
    row = session.get(EventOutbox, event_id)
    assert row is not None and row.event == "asset_status"
    assert row.data == {"asset_id": 1, "download": "error"}


def test_publish_rolls_back_when_business_transaction_rolls_back(session):
    job = JobQueue(job_type="download", payload={"asset_id": 2})
    session.add(job)
    session.flush()
    events.publish("asset_status", {"asset_id": 2}, db=session)
    session.rollback()
    assert session.scalars(select(EventOutbox)).all() == []


def test_events_since_supports_reconnect_cursor(session):
    first = events.publish("sync_done", {"subscription_id": 1}, db=session)
    second = events.publish("sync_done", {"subscription_id": 2}, db=session)
    session.commit()
    rows = events.events_since(session, first)
    assert [row[0] for row in rows] == [second]


def test_subscribe_replays_committed_events(client, session):
    row = JobQueue(job_type="download", payload={"asset_id": 9})
    session.add(row)
    session.flush()
    event_id = events.publish("asset_status", {"asset_id": 9, "download": "done"}, db=session)
    session.commit()
    bind = session.get_bind()
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)

    async def first_chunks():
        stream = events.subscribe(after_id=0, poll_seconds=0.01, session_factory=factory)
        try:
            return [await anext(stream), await anext(stream)]
        finally:
            await stream.aclose()

    chunks = asyncio.run(first_chunks())
    assert "event: hello" in chunks[0]
    assert f"id: {event_id}" in chunks[1]
    assert '"download": "done"' in chunks[1]


def test_job_logs_endpoint_is_scoped_and_incremental(client, session):
    job = JobQueue(job_type="download", payload={"asset_id": 11})
    session.add(job)
    session.flush()
    foundation.log_task(session, job.id, "download", "info", "领取任务")
    foundation.log_task(session, job.id, "download", "error", "下载失败", detail={"code": "network"})
    other = JobQueue(job_type="download", payload={"asset_id": 12})
    session.add(other)
    session.flush()
    foundation.log_task(session, other.id, "download", "info", "别的任务")
    session.commit()

    body = client.get(f"/api/pipeline/jobs/{job.id}/logs").json()
    assert [row["message"] for row in body["logs"]] == ["领取任务", "下载失败"]
    assert body["logs"][1]["detail"] == {"code": "network"}
    first_id = body["logs"][0]["id"]
    tail = client.get(f"/api/pipeline/jobs/{job.id}/logs", params={"after_id": first_id}).json()
    assert [row["message"] for row in tail["logs"]] == ["下载失败"]
    assert client.get("/api/pipeline/jobs/999999/logs").status_code == 404
