"""Capability manifest and shared crawl-task validation contracts."""

import asyncio

import pytest
from sqlalchemy import select

from app.models import CrawlTask, JobQueue, PlatformAccount, Subscription


def _account(session, platform="xhs", *, status="active", sync_enabled=True):
    account = PlatformAccount(
        platform=platform,
        display_name=f"{platform}-{status}",
        status=status,
        sync_enabled=sync_enabled,
    )
    session.add(account)
    session.commit()
    return account


def test_capability_manifest_is_consumed_by_platform_endpoint(client):
    rows = {row["platform"]: row for row in client.get("/api/platforms").json()}
    douyin = rows["douyin"]
    assert {item["value"] for item in douyin["source_types"]} == {
        "favorite", "collect_folder", "collection", "series", "creator"
    }
    assert {item["value"] for item in douyin["download_qualities"]} == {"best", "1080", "720"}
    assert douyin["keyword_crawl"] is True
    assert douyin["comments"] is True
    assert "play" in douyin["metrics"]
    assert rows["kuaishou"]["source_types"] == []
    assert "play" in rows["kuaishou"]["metrics"]
    assert rows["wechat_mp"]["metrics"] == []


def test_unsupported_subscription_source_is_rejected(client, session):
    account = _account(session, platform="xhs")
    response = client.post(
        "/api/subscriptions",
        json={"account_id": account.id, "source_type": "favorite", "remote_id": "f1"},
    )
    assert response.status_code == 422
    assert "不支持" in response.text


def test_unsupported_subscription_quality_is_rejected(client, session):
    account = _account(session, platform="douyin")
    response = client.post(
        "/api/subscriptions",
        json={
            "account_id": account.id,
            "source_type": "favorite",
            "remote_id": "f1",
            "download_quality": "audio",
        },
    )
    assert response.status_code == 422
    assert "不支持下载画质" in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"platform": "wechat_mp", "keyword": "x"},
        {"platform": "xhs", "keyword": "   "},
        {"platform": "xhs", "keyword": "x", "sort": "random"},
        {"platform": "xhs", "keyword": "x", "limit_per_run": 0},
        {"platform": "xhs", "keyword": "x", "limit_per_run": 501},
        {"platform": "xhs", "keyword": "x", "interval_minutes": 9},
        {"platform": "xhs", "keyword": "x", "comment_mode": "sometimes"},
    ],
)
def test_crawl_http_rejects_invalid_commands(client, body):
    assert client.post("/api/crawl-tasks", json=body).status_code == 422


def test_crawl_http_and_skills_share_the_same_validator(client):
    key = client.post("/api/ai/keys", json={"name": "agent", "scopes": ["operate"]}).json()["api_key"]
    headers = {"Authorization": f"Bearer {key}"}
    body = {"platform": "xhs", "keyword": "x", "limit_per_run": 0}
    assert client.post("/api/crawl-tasks", json=body).status_code == 422
    assert client.post("/api/skills/crawl-tasks", json=body, headers=headers).status_code == 422


def test_due_crawl_task_is_enqueued_once_and_backs_off(session, monkeypatch):
    import app.core.scheduler as scheduler

    account = _account(session, platform="xhs")
    task = CrawlTask(
        platform="xhs",
        keyword="露营",
        account_id=account.id,
        enabled=True,
        interval_minutes=60,
    )
    session.add(task)
    session.commit()
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: session)

    asyncio.run(scheduler.enqueue_due_crawl_tasks())
    asyncio.run(scheduler.enqueue_due_crawl_tasks())

    jobs = session.scalars(select(JobQueue).where(JobQueue.job_type == "crawl")).all()
    assert len(jobs) == 1
    stored = session.get(CrawlTask, task.id)
    assert stored.last_attempt_at is not None


def test_disabled_or_silent_crawl_task_is_not_enqueued(session, monkeypatch):
    import app.core.scheduler as scheduler

    silent = _account(session, platform="xhs", sync_enabled=False)
    disabled = CrawlTask(platform="xhs", keyword="a", enabled=False)
    blocked = CrawlTask(platform="xhs", keyword="b", account_id=silent.id, enabled=True)
    session.add_all([disabled, blocked])
    session.commit()
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: session)

    asyncio.run(scheduler.enqueue_due_crawl_tasks())
    assert session.scalars(select(JobQueue).where(JobQueue.job_type == "crawl")).all() == []


def test_subscription_interval_none_uses_global_range(session, monkeypatch):
    import app.core.scheduler as scheduler

    account = _account(session, platform="douyin")
    sub = Subscription(
        account_id=account.id,
        platform="douyin",
        source_type="favorite",
        remote_id="f1",
        interval_minutes=None,
        enabled=True,
    )
    session.add(sub)
    session.commit()
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        "app.core.sync_settings.get_sync_settings",
        lambda _db: {"interval_min_minutes": 300, "interval_max_minutes": 300},
    )

    asyncio.run(scheduler.enqueue_due_subscriptions())
    assert len(session.scalars(select(JobQueue).where(JobQueue.job_type == "sync")).all()) == 1
