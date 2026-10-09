"""阶段 0：内容必须带账号所有权，跨账号不互相吞素材。

这些测试直接驱动 service 编排，只 mock 平台出网边界；断言真实 SQLite 里的
content_item.account_id 与唯一性行为。
"""

import asyncio

from sqlalchemy import select

from app.core.crypto import encrypt_credential
from app.models import (
    AccountCredential,
    ContentItem,
    CrawlRun,
    CrawlTask,
    MediaAsset,
    PlatformAccount,
    Subscription,
)


def _account(session, platform="bilibili", name="号", status="active"):
    account = PlatformAccount(platform=platform, display_name=name, status=status, credential_kind="cookie")
    session.add(account)
    session.flush()
    account.credential = AccountCredential(ciphertext=encrypt_credential({"cookie": "c=1"}))
    return account


def _subscription(session, account, remote_id):
    sub = Subscription(
        account_id=account.id,
        platform=account.platform,
        source_type="favorite",
        remote_id=remote_id,
        title=remote_id,
        enabled=True,
    )
    session.add(sub)
    session.commit()
    return sub


def _item(external_id="V1"):
    return {
        "external_id": external_id,
        "content_type": "video",
        "title": "同一个视频",
        "author_name": "UP",
        "author_id": "u1",
        "url": "https://example.com/V1",
    }


def _patch_bilibili_fetch(monkeypatch, item):
    async def fake_fetch(self, credential, source_type, remote_id, limit):
        return [dict(item)]

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.fetch_source", fake_fetch, raising=False)


def test_same_video_from_two_accounts_creates_two_owned_contents(session, monkeypatch):
    """A/B 两个账号收到同一视频时，各自拥有独立素材，不能塌成一条。"""
    from app.services import sync_service

    account_a = _account(session, name="A")
    account_b = _account(session, name="B")
    sub_a = _subscription(session, account_a, "fav-a")
    sub_b = _subscription(session, account_b, "fav-b")
    _patch_bilibili_fetch(monkeypatch, _item())

    asyncio.run(sync_service.run_subscription_sync(session, sub_a))
    asyncio.run(sync_service.run_subscription_sync(session, sub_b))

    rows = session.scalars(
        select(ContentItem).where(ContentItem.platform == "bilibili", ContentItem.external_id == "V1")
    ).all()
    assert len(rows) == 2
    assert {row.account_id for row in rows} == {account_a.id, account_b.id}


def test_same_video_same_account_two_subscriptions_stays_one_content(session, monkeypatch):
    """同一账号内的两个订阅源看到同一视频，只保留一条内容（来源差异后续用 content_source 表达）。"""
    from app.services import sync_service

    account = _account(session)
    sub_a = _subscription(session, account, "fav-a")
    sub_b = _subscription(session, account, "fav-b")
    _patch_bilibili_fetch(monkeypatch, _item())

    asyncio.run(sync_service.run_subscription_sync(session, sub_a))
    asyncio.run(sync_service.run_subscription_sync(session, sub_b))

    rows = session.scalars(
        select(ContentItem).where(ContentItem.platform == "bilibili", ContentItem.external_id == "V1")
    ).all()
    assert len(rows) == 1
    assert rows[0].account_id == account.id


def test_unspecified_crawl_rotates_accounts_and_owns_content(session, monkeypatch):
    """未指定账号的采集要在健康账号间轮换，不能总取 id 最小账号。"""
    from app.modules import crawler
    from app.services import crawl_service

    account_a = _account(session, platform="xhs", name="A")
    account_b = _account(session, platform="xhs", name="B")
    task = CrawlTask(platform="xhs", keyword="露营", enabled=True)
    session.add(task)
    session.commit()

    async def fake_search(**kwargs):
        return [_item("xhs-1")]

    monkeypatch.setattr(crawler, "search", fake_search)

    run_a = asyncio.run(crawl_service.run_crawl_task(session, task.id))
    run_b = asyncio.run(crawl_service.run_crawl_task(session, task.id))

    assert run_a.status == "success" and run_b.status == "success"
    rows = session.scalars(select(ContentItem).where(ContentItem.external_id == "xhs-1")).all()
    assert {row.account_id for row in rows} == {account_a.id, account_b.id}
    assert {r.account_id for r in session.scalars(select(CrawlRun)).all()} == {account_a.id, account_b.id}


def test_article_import_without_account_uses_system_owner(session, monkeypatch):
    """未指定账号的文章导入必须有显式系统归属，不能产生无主内容。"""
    from app.modules import article_import
    from app.services import import_service

    async def fake_fetch(url):
        return {
            "external_id": "wx-1",
            "title": "文章",
            "author_name": "作者",
            "author_id": "a1",
            "url": url,
            "cover_url": "",
            "published_at": None,
            "html": "<p>x</p>",
            "text": "正文",
        }

    monkeypatch.setattr(article_import, "fetch_article", fake_fetch)
    asset, created = asyncio.run(import_service.import_wechat_article(session, "https://mp.weixin.qq.com/s/x"))

    assert created is True
    content = session.get(ContentItem, asset.content_id)
    assert content is not None and content.account_id is not None
    owner = session.get(PlatformAccount, content.account_id)
    assert owner is not None and owner.is_system is True


def test_assets_api_filters_by_account(client, session):
    """素材库按账号筛选时只能看到该账号自己的内容。"""
    account_a = _account(session, name="A")
    account_b = _account(session, name="B")
    for account, external_id in ((account_a, "V-A"), (account_b, "V-B")):
        content = ContentItem(
            account_id=account.id,
            platform="bilibili",
            external_id=external_id,
            content_type="video",
            title=external_id,
        )
        session.add(content)
        session.flush()
        session.add(MediaAsset(content_id=content.id, download_status="done"))
    session.commit()

    data = client.get("/api/assets", params={"account_id": account_a.id}).json()
    assert [row["account_id"] for row in data] == [account_a.id]
    assert [row["title"] for row in data] == ["V-A"]
