"""Queue identity, concurrent enqueue, and batch retry contracts."""

from app.core import queue
from app.models import JobQueue


def test_dedup_key_is_canonical_and_payload_sensitive():
    a = queue.dedup_key("sync", {"subscription_id": 1, "force": False})
    b = queue.dedup_key("sync", {"force": False, "subscription_id": 1})
    c = queue.dedup_key("sync", {"subscription_id": 1, "force": True})
    assert a == b
    assert a != c


def test_terminal_jobs_do_not_block_same_payload(session):
    first = queue.enqueue(session, "download", {"asset_id": 1})
    queue.complete(session, first)
    second = queue.enqueue(session, "download", {"asset_id": 1})
    assert second.id != first.id


def test_failed_terminal_job_releases_dedup_key(session):
    job = queue.enqueue(session, "download", {"asset_id": 1}, max_attempts=1)
    claimed = queue.claim(session, ["download"])
    queue.fail(session, claimed, "boom", retry=False)
    session.refresh(job)
    assert job.status == "error"
    assert job.dedup_key is None
    replacement = queue.enqueue(session, "download", {"asset_id": 1})
    assert replacement.id != job.id


def test_retry_sets_dedup_key_again(session):
    job = JobQueue(job_type="download", payload={"asset_id": 1}, status="error")
    session.add(job)
    session.commit()
    retried = queue.retry(session, job.id)
    assert retried is not None
    assert retried.status == "queued"
    assert retried.dedup_key == queue.dedup_key("download", {"asset_id": 1})


def test_retry_failed_uses_bounded_transactions(session, monkeypatch):
    session.add_all([
        JobQueue(job_type="download", payload={"asset_id": i}, status="error")
        for i in range(1, 8)
    ])
    session.commit()
    commits = {"n": 0}
    real_commit = session.commit

    def counting_commit():
        commits["n"] += 1
        return real_commit()

    monkeypatch.setattr(session, "commit", counting_commit)
    ids = queue.retry_failed(session, batch_size=3)
    assert len(ids) == 7
    assert commits["n"] == 3


def test_retry_failed_skips_legacy_duplicate_without_aborting_batch(session):
    active = queue.enqueue(session, "download", {"asset_id": 1})
    duplicate = JobQueue(job_type="download", payload={"asset_id": 1}, status="error")
    other = JobQueue(job_type="download", payload={"asset_id": 2}, status="error")
    session.add_all([duplicate, other])
    session.commit()
    # Keep the active row keyed so the failure row is the only candidate for
    # this identity; retry must skip it rather than create a duplicate.
    assert active.dedup_key is not None
    ids = queue.retry_failed(session)
    assert duplicate.id not in ids
    assert other.id in ids
    session.refresh(active)
    session.refresh(duplicate)
    assert active.status == "queued"
    assert duplicate.status == "error"


def test_retry_failed_keeps_one_per_identity_within_batch(session):
    first = JobQueue(job_type="crawl", payload={"task_id": 1}, status="error")
    second = JobQueue(job_type="crawl", payload={"task_id": 1}, status="error")
    session.add_all([first, second])
    session.commit()
    ids = queue.retry_failed(session, batch_size=10)
    assert ids == [first.id]
    session.refresh(second)
    assert second.status == "error"
