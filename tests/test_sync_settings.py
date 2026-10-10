"""订阅同步设置（设计 §A2）的执行层测试。

覆盖 `sync_service.run_subscription_sync` 拉取后的设置接入：
- min_play 过滤（有指标才判、无指标保留）；
- publish_after_days 过滤（ISO/date 解析、tz-safe、无发布时间保留）；
- max_items 截断（越界钳制到 1..500）；
- only_new=False 的刷新路径（覆盖更新已有内容字段，added 仍只计新增）；
- with_comments=True 覆盖全局评论门控（低热度也入队；仍限 douyin、仍去重）。
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import (
    AccountCredential,
    ContentItem,
    ContentMetricSnapshot,
    JobQueue,
    PlatformAccount,
    Subscription,
)
from app.services import sync_service


def _seed(session, *, platform="bilibili", credential=None, **sub_kwargs):
    from app.core.crypto import encrypt_credential

    acc = PlatformAccount(platform=platform, display_name="主号", credential_kind="cookie", status="active")
    session.add(acc)
    session.flush()
    acc.credential = AccountCredential(ciphertext=encrypt_credential(credential or {"sessdata": "x"}))
    kwargs = dict(
        account_id=acc.id,
        platform=platform,
        source_type="favorite",
        remote_id="fav1",
        title="收藏夹",
        enabled=True,
    )
    kwargs.update(sub_kwargs)
    sub = Subscription(**kwargs)
    session.add(sub)
    session.commit()
    return acc, sub


def _patch_fetch(monkeypatch, platform, items):
    async def fake_fetch(self, credential, source_type, remote_id, limit):
        return list(items)

    target = f"app.modules.{platform}.{platform.capitalize()}Adapter.fetch_source"
    monkeypatch.setattr(target, fake_fetch, raising=False)


def _run(session, sub):
    return asyncio.run(sync_service.run_subscription_sync(session, sub))


def _item(ext, **over):
    base = {
        "external_id": ext,
        "content_type": "video",
        "title": f"t-{ext}",
        "author_name": "UP主",
        "url": f"https://example.com/{ext}",
    }
    base.update(over)
    return base


def test_min_play_filters_low_play_and_keeps_missing_metrics(session, monkeypatch):
    _, sub = _seed(session, min_play=1000)
    _patch_fetch(
        monkeypatch,
        "bilibili",
        [
            _item("low", metrics={"play": 999}),
            _item("high", metrics={"play": 1000}),
            _item("none"),  # 无指标 → 保留
        ],
    )

    run = _run(session, sub)

    assert run.status == "success"
    assert run.fetched == 3  # fetched 仍报拉取原始条数
    assert run.added == 2
    ids = {c.external_id for c in session.scalars(select(ContentItem)).all()}
    assert ids == {"high", "none"}


def test_publish_after_days_drops_old_items_and_keeps_undated(session, monkeypatch):
    _, sub = _seed(session, publish_after_days=7)
    now = datetime.utcnow()
    _patch_fetch(
        monkeypatch,
        "bilibili",
        [
            _item("recent", published_at=(now - timedelta(days=1)).isoformat()),
            _item("old", published_at=(now - timedelta(days=30)).isoformat()),
            _item("dateonly", published_at=(now - timedelta(days=2)).strftime("%Y-%m-%d")),
            _item("undated"),
        ],
    )

    run = _run(session, sub)

    assert run.added == 3
    ids = {c.external_id for c in session.scalars(select(ContentItem)).all()}
    assert ids == {"recent", "dateonly", "undated"}


def test_publish_after_days_is_tz_safe(session, monkeypatch):
    _, sub = _seed(session, publish_after_days=7)
    old_aware = (datetime.utcnow() - timedelta(days=30)).replace(microsecond=0).isoformat() + "Z"
    _patch_fetch(monkeypatch, "bilibili", [_item("oldz", published_at=old_aware), _item("undated")])

    run = _run(session, sub)

    assert run.added == 1
    ids = {c.external_id for c in session.scalars(select(ContentItem)).all()}
    assert ids == {"undated"}


def test_max_items_truncates(session, monkeypatch):
    _, sub = _seed(session, max_items=2)
    _patch_fetch(monkeypatch, "bilibili", [_item(f"v{i}") for i in range(5)])

    run = _run(session, sub)

    assert run.fetched == 5
    assert run.added == 2
    assert len(session.scalars(select(ContentItem)).all()) == 2


def test_max_items_out_of_range_is_clamped(session, monkeypatch):
    _, sub = _seed(session, max_items=999)
    _patch_fetch(monkeypatch, "bilibili", [_item(f"v{i}") for i in range(5)])

    run = _run(session, sub)

    # 越界值按上限 500 钳制 → 全部 5 条入库（未被 0/负值误清空）
    assert run.added == 5


def test_only_new_false_refreshes_existing_fields(session, monkeypatch):
    _, sub = _seed(session, only_new=False)
    _patch_fetch(
        monkeypatch,
        "bilibili",
        [_item("v1", title="旧标题", url="https://old", cover_url="https://old-cover",
               author_name="旧作者", metrics={"play": 10})],
    )
    run1 = _run(session, sub)
    assert run1.added == 1

    _patch_fetch(
        monkeypatch,
        "bilibili",
        [_item("v1", title="新标题", url="https://new", cover_url="https://new-cover",
               author_name="新作者", metrics={"play": 99})],
    )
    run2 = _run(session, sub)

    assert run2.added == 0  # 已存在 → 不计新增
    content = session.scalar(select(ContentItem).where(ContentItem.external_id == "v1"))
    assert content.title == "新标题"
    assert content.url == "https://new"
    assert content.cover_url == "https://new-cover"
    assert content.author_name == "新作者"
    # 仍记录指标快照（两次运行各一条）
    snaps = session.scalars(
        select(ContentMetricSnapshot).where(ContentMetricSnapshot.content_id == content.id)
    ).all()
    assert len(snaps) == 2


def test_only_new_true_does_not_refresh_existing(session, monkeypatch):
    _, sub = _seed(session, only_new=True)
    _patch_fetch(monkeypatch, "bilibili", [_item("v1", title="旧标题")])
    _run(session, sub)
    _patch_fetch(monkeypatch, "bilibili", [_item("v1", title="新标题")])

    run2 = _run(session, sub)

    assert run2.added == 0
    content = session.scalar(select(ContentItem).where(ContentItem.external_id == "v1"))
    assert content.title == "旧标题"


def _comment_jobs(session):
    return session.scalars(select(JobQueue).where(JobQueue.job_type == "fetch_comments")).all()


def test_with_comments_true_bypasses_gate_for_low_heat(session, monkeypatch):
    _, sub = _seed(session, platform="douyin", with_comments=True)
    # 热度远低于默认门控下限（min_comments=20 / min_likes=100）
    _patch_fetch(monkeypatch, "douyin", [_item("aw1", metrics={"play": 5, "like": 1, "comment": 0})])

    run = _run(session, sub)

    assert run.status == "success"
    assert len(_comment_jobs(session)) == 1


def test_with_comments_false_keeps_gate(session, monkeypatch):
    _, sub = _seed(session, platform="douyin", with_comments=False)
    _patch_fetch(monkeypatch, "douyin", [_item("aw1", metrics={"play": 5, "like": 1, "comment": 0})])

    run = _run(session, sub)

    assert run.status == "success"
    assert _comment_jobs(session) == []


def test_with_comments_true_still_skips_unsupported_platform(session):
    """with_comments 也不能给不在评论白名单的平台入队（知乎暂无评论适配器）。"""
    from app.services.comment_gate import enqueue_comment_jobs

    c = ContentItem(account_id=1, platform="zhihu", external_id="z1", content_type="video")
    session.add(c)
    session.commit()
    n = enqueue_comment_jobs(
        session, [c], platform="zhihu",
        settings={"top_pct": 1.0, "budget": 10, "min_comments": 0, "min_likes": 0, "weight": 1},
        bypass_gate=True,
    )
    assert n == 0
    assert _comment_jobs(session) == []


def test_with_comments_is_idempotent(session, monkeypatch):
    _, sub = _seed(session, platform="douyin", with_comments=True)
    _patch_fetch(monkeypatch, "douyin", [_item("aw1", metrics={"play": 5, "like": 1, "comment": 0})])
    _run(session, sub)

    # 二次同步：内容已存在，且评论 job 仍在队列 → 不重复入队
    _run(session, sub)

    assert len(_comment_jobs(session)) == 1


def test_upsert_content_clamps_overlong_title(session):
    """平台返回超长文案（抖音长简介）不能撑爆 VARCHAR 列 —— 入库前按列宽截断。"""
    from app.models import PlatformAccount, Subscription
    from app.services.sync_service import _upsert_content

    acc = PlatformAccount(platform="douyin", display_name="a", status="active")
    session.add(acc)
    session.commit()
    sub = Subscription(
        id=9001, account_id=acc.id, platform="douyin", source_type="favorite",
        remote_id="r", title="收藏",
    )
    session.add(sub)
    session.commit()

    long_title = "长" * 2000
    item = {
        "external_id": "e1",
        "title": long_title,
        "author_name": "作" * 500,
        "url": "https://x/" + "u" * 3000,
        "cover_url": "https://c/" + "c" * 3000,
        "content_type": "video",
    }
    content, is_new = _upsert_content(session, sub, item)
    session.commit()
    assert is_new
    assert len(content.title) == 512
    assert len(content.author_name) == 128
    assert len(content.url) <= 1024
    assert len(content.cover_url) <= 1024
