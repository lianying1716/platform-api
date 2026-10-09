"""收尾修复：定时刷新、软删账号阻断、订阅校验、看板口径、SQLite FK。"""

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.core.crypto import decrypt_credential, encrypt_credential
from app.models import (
    AccountCredential,
    ContentItem,
    MediaAsset,
    PlatformAccount,
    Subscription,
)


def _account(session, platform="bilibili", name="A", status="active"):
    account = PlatformAccount(
        platform=platform, display_name=name, status=status, credential_kind="cookie"
    )
    session.add(account)
    session.flush()
    account.credential = AccountCredential(ciphertext=encrypt_credential({"cookie": "c=1"}))
    session.commit()
    return account


def test_scheduler_refreshes_bili_on_need_refresh(_engine, monkeypatch):
    """定时巡检也必须走自动刷新，而不是只有手动体检才刷新。"""
    import app.core.scheduler as scheduler
    from app.modules.base import CheckResult

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    setup = Session()
    account = _account(setup)
    account_id = account.id
    setup.close()

    async def fake_check(self, credential):
        return CheckResult(ok=True, message="在线", external_id="D", need_refresh=True)

    async def fake_refresh(self, credential):
        return {**credential, "sessdata": "NEW_S", "ac_time_value": "NEW_R"}

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.check", fake_check)
    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.refresh", fake_refresh)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    asyncio.run(scheduler.check_all_accounts())

    verify = Session()
    saved = decrypt_credential(verify.get(PlatformAccount, account_id).credential.ciphertext)
    verify.close()
    assert saved["sessdata"] == "NEW_S"
    assert saved["ac_time_value"] == "NEW_R"


def test_scheduler_skips_deleted_accounts(_engine, monkeypatch):
    """定时巡检不能把软删账号体检回 active。"""
    import app.core.scheduler as scheduler

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    setup = Session()
    account = _account(setup, status="deleted")
    account_id = account.id
    setup.close()

    async def must_not_check(self, credential):
        raise AssertionError("软删账号不应参与定时体检")

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.check", must_not_check)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    asyncio.run(scheduler.check_all_accounts())

    verify = Session()
    assert verify.get(PlatformAccount, account_id).status == "deleted"
    verify.close()


def test_sync_rejects_deleted_account(session, monkeypatch):
    """软删账号不得继续同步生产素材。"""
    from app.services import sync_service

    account = _account(session, status="deleted")
    sub = Subscription(
        account_id=account.id,
        platform=account.platform,
        source_type="favorite",
        remote_id="f",
        title="F",
        enabled=True,
    )
    session.add(sub)
    session.commit()

    async def fake_fetch(self, credential, source_type, remote_id, limit):
        return [{"external_id": "V", "url": "u", "content_type": "video"}]

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.fetch_source", fake_fetch, raising=False)
    run = asyncio.run(sync_service.run_subscription_sync(session, sub))

    assert run.status == "error"
    assert "停用" in run.error or "不可用" in run.error
    assert session.scalar(select(ContentItem)) is None


def test_content_cookie_empty_for_deleted_account(session):
    """下载/评论取凭证时也要阻止已停用账号。"""
    from app.workers.handlers import _content_cookie

    account = _account(session, status="deleted")
    content = ContentItem(account_id=account.id, platform="bilibili", external_id="V")
    assert _content_cookie(session, content) == ""


def test_create_subscription_rejects_deleted_account(client, session):
    account = _account(session, status="deleted")
    resp = client.post(
        "/api/subscriptions",
        json={"account_id": account.id, "source_type": "favorite", "remote_id": "f", "title": "F"},
    )
    assert resp.status_code in (404, 422)


def test_subscriptions_list_hides_deleted(client, session):
    account = _account(session)
    sub = Subscription(
        account_id=account.id,
        platform=account.platform,
        source_type="favorite",
        remote_id="f",
        title="F",
        enabled=False,
        deleted_at=datetime.utcnow(),
    )
    session.add(sub)
    session.commit()
    data = client.get("/api/subscriptions").json()
    assert data == []


def test_dashboard_account_filter_returns_single_account_total(client, session):
    account = _account(session, name="Only")
    content = ContentItem(account_id=account.id, platform="bilibili", external_id="V")
    session.add(content)
    session.flush()
    session.add(MediaAsset(content_id=content.id))
    session.commit()

    data = client.get("/api/dashboard/summary", params={"account_id": account.id}).json()
    assert data["accounts"]["total"] == 1


def test_sqlite_foreign_keys_enabled():
    from app.db import enable_sqlite_foreign_keys

    engine = create_engine("sqlite://")
    enable_sqlite_foreign_keys(engine)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_check_deleted_account_does_not_revive(client, session):
    """手动/定时体检都不能把已软删除账号改回 active。"""
    account = _account(session, status="deleted")
    resp = client.post(f"/api/accounts/{account.id}/check")
    assert resp.status_code == 409
    session.refresh(account)
    assert account.status == "deleted"


def test_sync_rejects_deleted_subscription(session, monkeypatch):
    """排队中的 sync job 在订阅被软删后必须拒绝执行。"""
    from app.services import sync_service

    account = _account(session)
    sub = Subscription(
        account_id=account.id,
        platform=account.platform,
        source_type="favorite",
        remote_id="f",
        title="F",
        enabled=True,
        deleted_at=datetime.utcnow(),
    )
    session.add(sub)
    session.commit()

    async def must_not_fetch(self, credential, source_type, remote_id, limit):
        raise AssertionError("已删除订阅不应拉取远端数据")

    monkeypatch.setattr(
        "app.modules.bilibili.BilibiliAdapter.fetch_source", must_not_fetch, raising=False
    )
    run = asyncio.run(sync_service.run_subscription_sync(session, sub))

    assert run.status == "error"
    assert "删除" in run.error
    assert session.scalar(select(ContentItem)) is None


def test_remote_sources_rejects_deleted_account(client, session):
    account = _account(session, status="deleted")
    resp = client.get(f"/api/accounts/{account.id}/remote-sources")
    assert resp.status_code == 422


def test_refresh_followings_rejects_deleted_account(client, session):
    account = _account(session, status="deleted")
    resp = client.post("/api/followed-creators/refresh", json={"account_id": account.id})
    assert resp.status_code == 422


def test_import_article_rejects_deleted_account(session, monkeypatch):
    from app.services import import_service

    account = _account(session, platform="wechat_mp", status="deleted")

    async def fake_fetch(url):
        raise AssertionError("账号校验失败时不应发起文章抓取")

    monkeypatch.setattr(import_service.article_import, "fetch_article", fake_fetch)
    with pytest.raises(ValueError):
        asyncio.run(
            import_service.import_wechat_article(
                session, "https://mp.weixin.qq.com/s/x", account_id=account.id
            )
        )


def test_update_subscription_rejects_cross_platform_account(client, session):
    account = _account(session, platform="bilibili")
    other = _account(session, platform="douyin", name="D")
    sub = Subscription(
        account_id=account.id,
        platform=account.platform,
        source_type="favorite",
        remote_id="f",
        title="F",
    )
    session.add(sub)
    session.commit()

    resp = client.put(
        f"/api/subscriptions/{sub.id}",
        json={
            "account_id": other.id,
            "source_type": "favorite",
            "remote_id": "f",
            "title": "F",
        },
    )
    assert resp.status_code == 422
    assert "平台" in resp.json()["detail"]
