"""阶段 4：关键词采集任务显式账号 + 公平轮换 + 运行追溯。"""

import asyncio

from sqlalchemy import select

from app.core.crypto import encrypt_credential
from app.models import AccountCredential, CrawlRun, CrawlTask, PlatformAccount


def _account(session, platform="xhs", name="号", status="active"):
    account = PlatformAccount(
        platform=platform,
        display_name=name,
        status=status,
        credential_kind="cookie",
    )
    session.add(account)
    session.flush()
    account.credential = AccountCredential(ciphertext=encrypt_credential({"cookie": "c=1"}))
    session.commit()
    return account


def _patch_search(monkeypatch, external_id="x1"):
    from app.modules import crawler

    async def fake_search(**kwargs):
        return [{"external_id": external_id, "content_type": "video", "title": "t", "url": "u"}]

    monkeypatch.setattr(crawler, "search", fake_search)


def test_create_task_with_specified_account(client, session):
    account = _account(session)
    resp = client.post(
        "/api/crawl-tasks",
        json={"platform": "xhs", "keyword": "露营", "account_id": account.id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_id"] == account.id


def test_create_task_rejects_wrong_platform_or_inactive_account(client, session):
    other = _account(session, platform="douyin", name="别的平台")
    expired = _account(session, platform="xhs", name="失效", status="expired")

    wrong = client.post(
        "/api/crawl-tasks",
        json={"platform": "xhs", "keyword": "k", "account_id": other.id},
    )
    assert wrong.status_code == 422

    inactive = client.post(
        "/api/crawl-tasks",
        json={"platform": "xhs", "keyword": "k", "account_id": expired.id},
    )
    assert inactive.status_code == 422


def test_specified_account_wins_over_rotation(session, monkeypatch):
    from app.services import crawl_service

    older = _account(session, name="更久未用")
    fixed = _account(session, name="指定账号")
    task = CrawlTask(platform="xhs", keyword="k", account_id=fixed.id, enabled=True)
    session.add(task)
    session.commit()
    _patch_search(monkeypatch, "fixed-1")

    run = asyncio.run(crawl_service.run_crawl_task(session, task.id))

    assert run.status == "success"
    assert run.account_id == fixed.id, "指定账号必须优先，不能被轮换抢走"


def test_inactive_specified_account_fails_readably(session, monkeypatch):
    from app.services import crawl_service

    account = _account(session, name="先正常")
    task = CrawlTask(platform="xhs", keyword="k", account_id=account.id, enabled=True)
    session.add(task)
    session.commit()
    account.status = "expired"
    session.commit()
    _patch_search(monkeypatch, "should-not-run")

    run = asyncio.run(crawl_service.run_crawl_task(session, task.id))

    assert run.status == "error"
    assert "账号" in run.error
    assert session.scalar(select(CrawlRun).where(CrawlRun.id == run.id)).account_id is None


def test_rotation_never_reuses_same_account_back_to_back(session, monkeypatch):
    from app.services import crawl_service

    a = _account(session, name="A")
    b = _account(session, name="B")
    task = CrawlTask(platform="xhs", keyword="k", enabled=True)
    session.add(task)
    session.commit()
    _patch_search(monkeypatch, "rot-1")

    first = asyncio.run(crawl_service.run_crawl_task(session, task.id))
    second = asyncio.run(crawl_service.run_crawl_task(session, task.id))

    assert {first.account_id, second.account_id} == {a.id, b.id}


def test_skill_create_task_accepts_account_id(client, session):
    account = _account(session, platform="xhs", name="Skill 指定号")
    created = client.post("/api/ai/keys", json={"name": "agent", "scopes": ["operate"]})
    headers = {"Authorization": f"Bearer {created.json()['api_key']}"}

    ok = client.post(
        "/api/skills/crawl-tasks",
        json={"platform": "xhs", "keyword": "k", "account_id": account.id},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text

    bad = client.post(
        "/api/skills/crawl-tasks",
        json={"platform": "xhs", "keyword": "k", "account_id": 999999},
        headers=headers,
    )
    assert bad.status_code == 422
