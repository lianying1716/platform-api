"""Scale regression: dedup and retry must not degrade with total queue size."""

import time
from datetime import datetime

from sqlalchemy import insert

from app.core import queue
from app.models import JobQueue


def test_dedup_lookup_is_indexed_and_retry_is_batched(session, monkeypatch):
    session.execute(
        insert(JobQueue),
        [
            {
                "job_type": "download",
                "payload": {"asset_id": -i},
                "status": "done",
                "priority": 100,
                "attempts": 1,
                "max_attempts": 3,
                "error": "",
                "run_after": datetime.utcnow(),
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            for i in range(10_000)
        ],
    )
    session.commit()

    start = time.perf_counter()
    active = queue.enqueue(session, "download", {"asset_id": 1})
    duplicate = queue.enqueue(session, "download", {"asset_id": 1})
    elapsed = time.perf_counter() - start
    assert duplicate.id == active.id
    assert elapsed < 2.0, f"dedup took {elapsed:.2f}s with 10k rows"

    session.execute(
        insert(JobQueue),
        [
            {
                "job_type": "crawl",
                "payload": {"task_id": i},
                "status": "error",
                "priority": 100,
                "attempts": 3,
                "max_attempts": 3,
                "error": "boom",
                "run_after": datetime.utcnow(),
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            for i in range(10_000)
        ],
    )
    session.commit()

    calls = {"n": 0}
    real_commit = session.commit

    def counting_commit():
        calls["n"] += 1
        return real_commit()

    monkeypatch.setattr(session, "commit", counting_commit)
    start = time.perf_counter()
    ids = queue.retry_failed(session, job_type="crawl", batch_size=500)
    elapsed = time.perf_counter() - start
    assert len(ids) == 10_000
    assert calls["n"] == 20
    assert elapsed < 10.0, f"retry took {elapsed:.2f}s for 10k rows"
