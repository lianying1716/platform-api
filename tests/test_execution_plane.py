"""Execution-plane contract: commands, run traceability and crash recovery."""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

from app.domain.jobs import JobOutcome, RetryClass
from app.models import (
    AccountCredential,
    AiModel,
    AiProvider,
    ContentItem,
    CrawlRun,
    CrawlTask,
    JobQueue,
    MediaAsset,
    ModelCallAttempt,
    PlatformAccount,
    Subscription,
    SyncRun,
)


def _account(session, platform="bilibili"):
    from app.core.crypto import encrypt_credential

    acc = PlatformAccount(
        platform=platform, display_name="主号", credential_kind="cookie", status="active"
    )
    session.add(acc)
    session.flush()
    acc.credential = AccountCredential(ciphertext=encrypt_credential({"cookie": "c=1"}))
    session.commit()
    return acc


def _subscription(session, acc):
    sub = Subscription(
        account_id=acc.id,
        platform=acc.platform,
        source_type="favorite",
        remote_id="fav1",
        title="收藏夹",
        enabled=True,
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)
    return sub


def test_manual_sync_returns_202_and_api_does_not_call_platform(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    sub = _subscription(session, acc)
    called = {"n": 0}

    async def fake_fetch(*args, **kwargs):
        called["n"] += 1
        return []

    monkeypatch.setattr(BilibiliAdapter, "fetch_source", fake_fetch)
    response = client.post(f"/api/subscriptions/{sub.id}/sync")

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    job = session.get(JobQueue, response.json()["job_id"])
    assert job is not None and job.payload == {"subscription_id": sub.id, "force": True}
    assert called["n"] == 0, "API 进程不得执行平台抓取"


def test_manual_sync_force_reaches_service(client, session, monkeypatch):
    import app.workers.handlers as handlers
    from app.services.sync_service import run_subscription_sync

    acc = _account(session)
    sub = _subscription(session, acc)
    seen = {}

    async def fake_fetch(self, credential, source_type, remote_id, limit):
        seen["calls"] = seen.get("calls", 0) + 1
        return [{"external_id": "fresh", "content_type": "video", "title": "新", "url": "u"}]

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.fetch_source", fake_fetch)
    # Prime the creator-video cache with an empty result; the manual path must bypass it.
    sub.source_type = "creator"
    sub.sync_videos = True
    session.commit()
    asyncio.run(run_subscription_sync(session, sub, force=False))
    before = seen["calls"]

    response = client.post(f"/api/subscriptions/{sub.id}/sync")
    job = session.get(JobQueue, response.json()["job_id"])
    outcome = asyncio.run(handlers.handle_sync(session, job.payload, job))

    assert outcome.ok is True
    assert seen["calls"] == before + 1, "立即同步必须跳过 15 分钟缓存"
    run = session.scalar(select(SyncRun).where(SyncRun.job_id == job.id))
    assert run is not None and run.status == "success" and run.job_id == job.id


def test_runner_exception_reconciles_asset_immediately(_engine, monkeypatch):
    """A handler exception must not leave an asset stuck in downloading."""
    import app.workers.runner as runner
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    acc = PlatformAccount(platform="douyin", display_name="a")
    db.add(acc)
    db.flush()
    content = ContentItem(account_id=acc.id, platform="douyin", external_id="e1", url="u")
    db.add(content)
    db.flush()
    asset = MediaAsset(content_id=content.id, download_status="downloading")
    db.add(asset)
    db.flush()
    db.add(
        JobQueue(
            job_type="download",
            payload={"asset_id": asset.id},
            max_attempts=1,
        )
    )
    db.commit()
    asset_id = asset.id
    db.close()

    async def boom(db, payload):
        raise RuntimeError("disk full")

    monkeypatch.setattr(runner, "SessionLocal", Session)
    monkeypatch.setattr(runner, "HANDLERS", {"download": boom})
    assert asyncio.run(runner._run_one()) is True

    db = Session()
    assert db.get(MediaAsset, asset_id).download_status == "error"
    assert db.scalar(select(JobQueue)).status == "error"
    db.close()


def test_runner_failed_probe_leaves_testing_state(_engine, monkeypatch):
    import app.workers.runner as runner
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    provider = AiProvider(
        name="p", protocol="openai", base_url="https://api.example.com", api_key_encrypted="x"
    )
    db.add(provider)
    db.flush()
    model = AiModel(
        provider_id=provider.id,
        model_key="m",
        probe_status="testing",
        probe_started_at=datetime.utcnow(),
    )
    db.add(model)
    db.flush()
    db.add(JobQueue(job_type="probe_model", payload={"model_id": model.id}, max_attempts=1))
    db.commit()
    model_id = model.id
    db.close()

    async def fail(db, payload, job=None):
        return JobOutcome.failure("probe boom", retry=RetryClass.NEVER)

    monkeypatch.setattr(runner, "SessionLocal", Session)
    monkeypatch.setattr(runner, "HANDLERS", {"probe_model": fail})
    assert asyncio.run(runner._run_one()) is True

    db = Session()
    model = db.get(AiModel, model_id)
    assert model.probe_status == "failed"
    assert model.probe_started_at is None
    db.close()


def test_stale_sync_run_is_reconciled_and_retryable(session):
    from app.services.run_reconciler import reconcile_runs

    acc = _account(session)
    sub = _subscription(session, acc)
    job = JobQueue(job_type="sync", payload={"subscription_id": sub.id}, status="error")
    session.add(job)
    session.flush()
    run = SyncRun(
        subscription_id=sub.id,
        job_id=job.id,
        status="running",
        started_at=datetime.utcnow() - timedelta(hours=2),
        heartbeat_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(run)
    session.commit()

    assert reconcile_runs(session, older_than_minutes=30)["sync"] == 1
    session.refresh(run)
    assert run.status == "error" and run.finished_at is not None and run.heartbeat_at is None
    assert "可重新触发" in run.error
    assert job.status == "error"


def test_stale_crawl_run_is_reconciled(session):
    from app.services.run_reconciler import reconcile_runs

    task = CrawlTask(platform="douyin", keyword="k")
    session.add(task)
    session.flush()
    run = CrawlRun(
        task_id=task.id,
        status="running",
        started_at=datetime.utcnow() - timedelta(hours=2),
        heartbeat_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(run)
    session.commit()

    assert reconcile_runs(session, older_than_minutes=30)["crawl"] == 1
    session.refresh(run)
    assert run.status == "error" and run.finished_at is not None


def test_healthy_heartbeat_keeps_run_running(session):
    from app.services.run_reconciler import reconcile_runs

    acc = _account(session)
    sub = _subscription(session, acc)
    job = JobQueue(job_type="sync", payload={"subscription_id": sub.id}, status="running")
    session.add(job)
    session.flush()
    job.locked_at = datetime.utcnow() - timedelta(hours=3)
    job.heartbeat_at = datetime.utcnow()
    run = SyncRun(
        subscription_id=sub.id,
        job_id=job.id,
        status="running",
        started_at=datetime.utcnow() - timedelta(hours=3),
        heartbeat_at=datetime.utcnow(),
    )
    session.add(run)
    session.commit()

    assert reconcile_runs(session, older_than_minutes=30) == {}
    session.refresh(run)
    assert run.status == "running"


def test_stale_probe_is_failed(session):
    from app.services.run_reconciler import reconcile_runs

    provider = AiProvider(
        name="p", protocol="openai", base_url="https://api.example.com", api_key_encrypted="x"
    )
    session.add(provider)
    session.flush()
    model = AiModel(
        provider_id=provider.id,
        model_key="m",
        probe_status="testing",
        probe_started_at=datetime.utcnow() - timedelta(hours=1),
    )
    session.add(model)
    session.commit()

    assert reconcile_runs(session, older_than_minutes=30)["probe"] == 1
    session.refresh(model)
    assert model.probe_status == "failed" and model.probe_started_at is None


def test_sync_failure_does_not_leave_job_done(_engine, monkeypatch):
    import app.workers.runner as runner
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    acc = PlatformAccount(platform="bilibili", display_name="a")
    db.add(acc)
    db.flush()
    sub = Subscription(
        account_id=acc.id,
        platform="bilibili",
        source_type="favorite",
        remote_id="f",
        enabled=True,
    )
    db.add(sub)
    db.flush()
    db.add(JobQueue(job_type="sync", payload={"subscription_id": sub.id}, max_attempts=1))
    db.commit()
    db.close()

    async def failed_sync(db, payload, job=None):
        return JobOutcome.failure("账号已失效", retry=RetryClass.NEVER, code="sync_failed")

    monkeypatch.setattr(runner, "SessionLocal", Session)
    monkeypatch.setattr(runner, "HANDLERS", {"sync": failed_sync})
    assert asyncio.run(runner._run_one()) is True

    db = Session()
    assert db.scalar(select(JobQueue)).status == "error"
    db.close()


def test_real_sync_service_error_marks_job_error_not_done(_engine, monkeypatch):
    """End-to-end through the real handler: a sync_run error must fail the job."""
    import app.workers.runner as runner
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    # No credential is attached, so the real sync service returns an error run.
    acc = PlatformAccount(platform="bilibili", display_name="a", status="active")
    db.add(acc)
    db.flush()
    sub = Subscription(
        account_id=acc.id,
        platform="bilibili",
        source_type="favorite",
        remote_id="f",
        enabled=True,
    )
    db.add(sub)
    db.flush()
    db.add(JobQueue(job_type="sync", payload={"subscription_id": sub.id}, max_attempts=1))
    db.commit()
    sub_id = sub.id
    db.close()

    monkeypatch.setattr(runner, "SessionLocal", Session)
    assert asyncio.run(runner._run_one()) is True

    db = Session()
    job = db.scalar(select(JobQueue))
    run = db.scalar(select(SyncRun).where(SyncRun.subscription_id == sub_id))
    assert job.status == "error" and "凭证" in job.error
    assert run is not None and run.status == "error" and run.job_id == job.id
    db.close()


def test_real_crawl_service_error_marks_job_error_not_done(_engine, monkeypatch):
    import app.workers.runner as runner
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    task = CrawlTask(platform="douyin", keyword="k")
    db.add(task)
    db.flush()
    db.add(JobQueue(job_type="crawl", payload={"task_id": task.id}, max_attempts=1))
    db.commit()
    task_id = task.id
    db.close()

    monkeypatch.setattr(runner, "SessionLocal", Session)
    assert asyncio.run(runner._run_one()) is True

    db = Session()
    job = db.scalar(select(JobQueue))
    run = db.scalar(select(CrawlRun).where(CrawlRun.task_id == task_id))
    assert job.status == "error" and "账号" in job.error
    assert run is not None and run.status == "error" and run.job_id == job.id
    db.close()


def test_stale_model_attempt_is_reconciled(session):
    from app.services.run_reconciler import reconcile_runs

    row = ModelCallAttempt(
        attempt_group_id="mg_stale",
        work_type="text_analysis",
        operation="analyze",
        role="primary",
        status="running",
        created_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(row)
    session.commit()

    assert reconcile_runs(session, older_than_minutes=30)["model_attempt"] == 1
    session.refresh(row)
    assert row.status == "error"
    assert row.error_class == "stale_running"
    assert row.finished_at is not None


def test_fresh_model_attempt_is_not_reconciled(session):
    from app.services.run_reconciler import reconcile_runs

    row = ModelCallAttempt(
        attempt_group_id="mg_fresh",
        work_type="text_analysis",
        operation="analyze",
        role="primary",
        status="running",
        created_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()

    assert reconcile_runs(session, older_than_minutes=30) == {}
    session.refresh(row)
    assert row.status == "running"


def test_model_attempt_owned_by_live_job_is_left_running(session):
    from app.services.run_reconciler import reconcile_runs

    job = JobQueue(
        job_type="analyze",
        payload={"asset_id": 1},
        status="running",
        heartbeat_at=datetime.utcnow(),
    )
    session.add(job)
    session.flush()
    row = ModelCallAttempt(
        attempt_group_id="mg_live",
        work_type="text_analysis",
        operation="analyze",
        job_id=job.id,
        role="primary",
        status="running",
        created_at=datetime.utcnow() - timedelta(hours=2),
    )
    session.add(row)
    session.commit()

    assert reconcile_runs(session, older_than_minutes=30) == {}
    session.refresh(row)
    assert row.status == "running"
