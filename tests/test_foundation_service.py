"""架构地基写入服务测试（app/services/foundation.py + 三处接线）。

覆盖：
- creator upsert：幂等、非空字段不覆盖、空 author_id 返回 None；
- raw_payload：落库、超限截断、空 external_id no-op、**绝不抛异常**；
- task_log：落库、**绝不抛异常**；
- crawl / sync：每条内容顺带写 creator + raw 行；
- runner：成功/失败路径各写 task_log；
- 隔离：地基 helper 抛异常**不翻转** crawl/sync/任务的成功态。
"""

import pytest
import asyncio

from sqlalchemy import select

from app.services import foundation


# ---------------------------------------------------------------------------
# upsert_creator
# ---------------------------------------------------------------------------


def test_upsert_creator_inserts_and_is_idempotent(session):
    from app.models import CreatorProfile

    p1 = foundation.upsert_creator(
        session, "douyin", "u1", author_name="阿一", follower_count=100
    )
    session.commit()
    assert p1 is not None and p1.id is not None
    assert p1.last_synced_at is not None

    # 同一 (platform, author_id) 再 upsert：更新而不是新增
    p2 = foundation.upsert_creator(
        session, "douyin", "u1", author_name="阿一改名", follower_count=250
    )
    session.commit()
    assert p2.id == p1.id
    assert session.query(CreatorProfile).count() == 1
    assert p2.author_name == "阿一改名" and p2.follower_count == 250

    # 不同平台同一 author_id 是不同创作者
    foundation.upsert_creator(session, "bilibili", "u1", author_name="另一个")
    session.commit()
    assert session.query(CreatorProfile).count() == 2


def test_upsert_creator_does_not_clobber_with_empty(session):
    foundation.upsert_creator(
        session,
        "xhs",
        "u9",
        author_name="作者",
        avatar="http://a.png",
        bio="简介",
        follower_count=42,
        homepage_url="http://h",
        raw={"nickname": "作者"},
    )
    session.commit()

    # 第二次只带默认空值：已有字段不能被冲成空
    p = foundation.upsert_creator(session, "xhs", "u9")
    session.commit()
    assert p.author_name == "作者"
    assert p.avatar == "http://a.png"
    assert p.bio == "简介"
    assert p.follower_count == 42
    assert p.homepage_url == "http://h"
    assert p.raw == {"nickname": "作者"}


def test_upsert_creator_empty_author_id_returns_none(session):
    from app.models import CreatorProfile

    assert foundation.upsert_creator(session, "douyin", "") is None
    assert foundation.upsert_creator(session, "douyin", None) is None
    session.commit()
    assert session.query(CreatorProfile).count() == 0


def test_upsert_creator_never_raises(session, monkeypatch):
    """helper 内部异常（如建表/写入失败）必须被吞掉，返回 None 而不是上抛。"""

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("creator boom")

    monkeypatch.setattr(foundation, "CreatorProfile", Boom)
    assert foundation.upsert_creator(session, "douyin", "u1", author_name="x") is None


# ---------------------------------------------------------------------------
# record_raw
# ---------------------------------------------------------------------------


def test_record_raw_inserts(session):
    from app.models import ContentItem, RawPayload

    content = ContentItem(account_id=1, platform="douyin", external_id="aw1")
    session.add(content)
    session.flush()

    foundation.record_raw(
        session, "douyin", "crawl", "aw1", {"title": "标题", "n": 1}, content_id=content.id
    )
    session.commit()

    row = session.scalar(select(RawPayload))
    assert row is not None
    assert row.platform == "douyin" and row.source == "crawl" and row.external_id == "aw1"
    assert row.payload == {"title": "标题", "n": 1}
    assert row.content_id == content.id
    assert row.captured_at is not None


def test_record_raw_truncates_oversized_payload(session):
    import json

    from app.models import RawPayload

    big = {"blob": "x" * 5000, "meta": {"k": "v"}}
    foundation.record_raw(session, "douyin", "crawl", "big1", big, max_bytes=200)
    session.commit()

    row = session.scalar(select(RawPayload).where(RawPayload.external_id == "big1"))
    assert row is not None
    assert row.payload.get("_truncated") is True
    assert row.payload.get("_original_bytes", 0) > 200
    # 整行序列化后必须仍在上限内（防止 JSON 列被撑爆）
    assert len(json.dumps(row.payload, ensure_ascii=False).encode("utf-8")) <= 200


def test_record_raw_empty_external_id_is_noop(session):
    from app.models import RawPayload

    foundation.record_raw(session, "douyin", "crawl", "", {"a": 1})
    session.commit()
    assert session.query(RawPayload).count() == 0


def test_record_raw_never_raises(session, monkeypatch):
    """payload 无法序列化 / 落库失败都不得上抛到调用方。"""

    # 1) 不可序列化的对象：退化为字符串，仍然落库
    foundation.record_raw(session, "douyin", "crawl", "e1", {"bad": object()})
    session.commit()

    # 2) 连 json.dumps 都炸：不抛异常
    def boom(*a, **k):
        raise RuntimeError("serialize boom")

    monkeypatch.setattr(foundation.json, "dumps", boom)
    foundation.record_raw(session, "douyin", "crawl", "e2", {"a": 1})  # 不抛

    # 3) DB 写入失败：不抛异常，且不破坏 session
    class BoomRow:
        def __init__(self, *a, **k):
            raise RuntimeError("row boom")

    monkeypatch.setattr(foundation, "RawPayload", BoomRow)
    foundation.record_raw(session, "douyin", "crawl", "e3", {"a": 1})  # 不抛


# ---------------------------------------------------------------------------
# log_task
# ---------------------------------------------------------------------------


def test_log_task_inserts(session):
    from app.models import TaskLog

    foundation.log_task(
        session, 7, "download", "error", "下载失败", detail={"error": "boom"}, actor="worker"
    )
    session.commit()

    row = session.scalar(select(TaskLog))
    assert row is not None
    assert row.job_id == 7 and row.job_type == "download"
    assert row.level == "error" and row.message == "下载失败"
    assert row.detail == {"error": "boom"} and row.actor == "worker"
    assert row.created_at is not None


def test_log_task_never_raises(session, monkeypatch):
    class BoomRow:
        def __init__(self, *a, **k):
            raise RuntimeError("log boom")

    monkeypatch.setattr(foundation, "TaskLog", BoomRow)
    foundation.log_task(session, None, "sync", "info", "x")  # 不抛


# ---------------------------------------------------------------------------
# crawl / sync 接线
# ---------------------------------------------------------------------------


def _seed_subscription(db):
    from app.core.crypto import encrypt_credential
    from app.models import AccountCredential, PlatformAccount, Subscription

    acc = PlatformAccount(
        platform="bilibili", display_name="主号", credential_kind="cookie", status="active"
    )
    db.add(acc)
    db.flush()
    acc.credential = AccountCredential(ciphertext=encrypt_credential({"sessdata": "x"}))
    sub = Subscription(
        account_id=acc.id,
        platform="bilibili",
        source_type="favorite",
        remote_id="fav1",
        title="收藏夹",
        enabled=True,
    )
    db.add(sub)
    db.commit()
    return sub


def _seed_crawl_account(db, platform="douyin"):
    from app.core.crypto import encrypt_credential
    from app.models import AccountCredential, PlatformAccount

    account = PlatformAccount(
        platform=platform,
        display_name="采集号",
        credential_kind="cookie",
        status="active",
    )
    db.add(account)
    db.flush()
    account.credential = AccountCredential(ciphertext=encrypt_credential({"cookie": "c=1"}))
    db.commit()
    return account


def test_crawl_persists_creator_and_raw(session, monkeypatch):
    from app.models import CrawlTask, CreatorProfile, RawPayload
    from app.services import crawl_service

    task = CrawlTask(platform="douyin", keyword="k", limit_per_run=10)
    session.add(task)
    _seed_crawl_account(session, "douyin")
    session.commit()

    async def fake_search(**kw):
        return [
            {
                "external_id": "aw1",
                "content_type": "video",
                "title": "标题",
                "author_id": "a1",
                "author_name": "作者A",
                "url": "u",
                "metrics": {"play": 1},
            }
        ]

    monkeypatch.setattr("app.modules.crawler.search", fake_search)
    run = asyncio.run(crawl_service.run_crawl_task(session, task.id))
    assert run.status == "success" and run.added == 1

    profile = session.scalar(
        select(CreatorProfile).where(
            CreatorProfile.platform == "douyin", CreatorProfile.author_id == "a1"
        )
    )
    assert profile is not None and profile.author_name == "作者A"
    raw = session.scalar(select(RawPayload).where(RawPayload.external_id == "aw1"))
    assert raw is not None and raw.source == "crawl" and raw.payload["title"] == "标题"


def test_sync_persists_creator_and_raw(session, monkeypatch):
    from app.models import CreatorProfile, RawPayload
    from app.services import sync_service

    sub = _seed_subscription(session)

    async def fake_fetch(self, credential, source_type, remote_id, limit):
        return [
            {
                "external_id": "BV1",
                "content_type": "video",
                "title": "视频",
                "author_id": "up1",
                "author_name": "UP主",
                "url": "u",
                "metrics": {"play": 1},
            }
        ]

    monkeypatch.setattr(
        "app.modules.bilibili.BilibiliAdapter.fetch_source", fake_fetch, raising=False
    )
    run = asyncio.run(sync_service.run_subscription_sync(session, sub))
    assert run.status == "success" and run.added == 1

    profile = session.scalar(
        select(CreatorProfile).where(CreatorProfile.author_id == "up1")
    )
    assert profile is not None and profile.author_name == "UP主"
    raw = session.scalar(select(RawPayload).where(RawPayload.external_id == "BV1"))
    assert raw is not None and raw.source == "subscription"


def test_crawl_survives_foundation_failure(session, monkeypatch):
    """I1：地基 helper 抛异常不得翻转采集成功态，也不得丢掉已入库内容。"""
    from app.models import ContentItem, CrawlTask
    from app.services import crawl_service

    task = CrawlTask(platform="douyin", keyword="k", limit_per_run=10)
    session.add(task)
    _seed_crawl_account(session, "douyin")
    session.commit()

    async def fake_search(**kw):
        return [
            {
                "external_id": "awX",
                "content_type": "video",
                "url": "u",
                "author_id": "a1",
                "author_name": "A",
                "metrics": {"play": 1},
            }
        ]

    monkeypatch.setattr("app.modules.crawler.search", fake_search)

    def boom(*a, **k):
        raise RuntimeError("foundation boom")

    monkeypatch.setattr(foundation, "upsert_creator", boom)
    monkeypatch.setattr(foundation, "record_raw", boom)

    run = asyncio.run(crawl_service.run_crawl_task(session, task.id))
    assert run.status == "success" and run.added == 1
    assert session.scalar(
        select(ContentItem).where(ContentItem.external_id == "awX")
    ) is not None


def test_sync_survives_foundation_failure(session, monkeypatch):
    """I1：地基 helper 抛异常不得翻转同步成功态，也不得丢掉已入库内容。"""
    from app.models import ContentItem
    from app.services import sync_service

    sub = _seed_subscription(session)

    async def fake_fetch(self, credential, source_type, remote_id, limit):
        return [
            {
                "external_id": "BVX",
                "content_type": "video",
                "url": "u",
                "author_id": "up1",
                "author_name": "UP",
                "metrics": {"play": 1},
            }
        ]

    monkeypatch.setattr(
        "app.modules.bilibili.BilibiliAdapter.fetch_source", fake_fetch, raising=False
    )

    def boom(*a, **k):
        raise RuntimeError("foundation boom")

    monkeypatch.setattr(foundation, "upsert_creator", boom)
    monkeypatch.setattr(foundation, "record_raw", boom)

    run = asyncio.run(sync_service.run_subscription_sync(session, sub))
    assert run.status == "success" and run.added == 1
    assert session.scalar(
        select(ContentItem).where(ContentItem.external_id == "BVX")
    ) is not None


# ---------------------------------------------------------------------------
# runner 接线
# ---------------------------------------------------------------------------


def _runner_sessionmaker(_engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def _enqueue_fake_job(_engine):
    from app.models import JobQueue

    S = _runner_sessionmaker(_engine)
    db = S()
    db.add(JobQueue(job_type="fake", payload={}))
    db.commit()
    db.close()


def test_runner_writes_task_log_on_success(_engine, monkeypatch):
    import app.workers.runner as runner
    from app.models import JobQueue, TaskLog

    _enqueue_fake_job(_engine)

    async def ok(db, payload):
        return None

    monkeypatch.setattr(runner, "SessionLocal", _runner_sessionmaker(_engine))
    monkeypatch.setattr(runner, "HANDLERS", {"fake": ok})
    assert asyncio.run(runner._run_one()) is True

    db = _runner_sessionmaker(_engine)()
    logs = db.scalars(select(TaskLog).order_by(TaskLog.id)).all()
    assert [log.level for log in logs] == ["info", "info"]
    assert all(log.job_type == "fake" and log.actor == "worker" for log in logs)
    assert "领取" in logs[0].message and "完成" in logs[1].message
    job = db.scalar(select(JobQueue))
    assert job.status == "done"
    db.close()


def test_runner_writes_task_log_on_failure(_engine, monkeypatch):
    import app.workers.runner as runner
    from app.models import TaskLog

    _enqueue_fake_job(_engine)

    async def boom(db, payload):
        raise RuntimeError("handler boom")

    monkeypatch.setattr(runner, "SessionLocal", _runner_sessionmaker(_engine))
    monkeypatch.setattr(runner, "HANDLERS", {"fake": boom})
    assert asyncio.run(runner._run_one()) is True

    db = _runner_sessionmaker(_engine)()
    logs = db.scalars(select(TaskLog).order_by(TaskLog.id)).all()
    assert [log.level for log in logs] == ["info", "error"]
    assert "handler boom" in logs[1].message
    assert logs[1].detail.get("error") == "handler boom"
    db.close()


def test_runner_survives_task_log_failure(_engine, monkeypatch):
    """I1：task_log 写入抛异常不得翻转任务结果。"""
    import app.workers.runner as runner
    from app.models import JobQueue

    _enqueue_fake_job(_engine)

    async def ok(db, payload):
        return None

    def boom(*a, **k):
        raise RuntimeError("log boom")

    monkeypatch.setattr(runner, "SessionLocal", _runner_sessionmaker(_engine))
    monkeypatch.setattr(runner, "HANDLERS", {"fake": ok})
    monkeypatch.setattr(foundation, "log_task", boom)
    assert asyncio.run(runner._run_one()) is True

    db = _runner_sessionmaker(_engine)()
    assert db.scalar(select(JobQueue)).status == "done"
    db.close()


def test_in_savepoint_isolates_real_flush_error(session):
    """核心风险点：地基写入触发**真实 DB 级 flush 错误**时，savepoint 必须隔离。

    若把 `_in_savepoint` 换成直接 `op()`（去掉 savepoint），则 op 内 flush 抛错会让 session
    进入 INACTIVE，后续 commit 抛 PendingRollbackError、外层已 flush 的 content_item 丢失；
    有 savepoint 时则只回滚保存点，外层事务完好。
    """
    from app.models import ContentItem, TaskLog
    from app.services import foundation

    session.add(ContentItem(account_id=1, platform="douyin", external_id="outer1", url="u1"))
    session.flush()  # 外层已写入，等价于采集循环中已入库的 item

    def bad_op():
        # job_type 为 NOT NULL；赋 None 会在 flush 时触发 DB IntegrityError。
        session.add(TaskLog(job_type=None, level="info"))
        session.flush()

    with pytest.raises(Exception):
        foundation._in_savepoint(session, bad_op)

    assert session.is_active  # savepoint 回滚后 session 仍可用
    session.commit()  # 若能提交，说明外层事务没被整段回滚
    assert session.scalar(select(ContentItem).where(ContentItem.external_id == "outer1")) is not None


def test_foundation_clamps_long_fields(session):
    """超长字符串按列宽截断，PG 下不会因 StringDataRightTruncation 丢掉整行。"""
    from app.services import foundation

    p = foundation.upsert_creator(
        session, "douyin", "long1", author_name="名" * 300, avatar="a" * 900, homepage_url="h" * 900
    )
    assert p is not None
    session.commit()
    assert len(p.author_name) <= 128 and len(p.avatar) <= 512 and len(p.homepage_url) <= 512


def test_analytics_creators_prefers_creator_profile(client, _engine):
    """作者维度端点：有 creator_profile 时补粉丝/认证信息，无则回退 group-by。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import ContentItem, CreatorProfile

    Session = sessionmaker(bind=_engine)
    db = Session()
    db.add(ContentItem(account_id=1, platform="douyin", external_id="c1", author_id="au1", author_name="原名", url="u1"))
    db.add(ContentItem(account_id=1, platform="douyin", external_id="c2", author_id="au2", author_name="无档案", url="u2"))
    db.add(CreatorProfile(platform="douyin", author_id="au1", author_name="官方名", follower_count=12345, verified=True))
    db.commit()
    db.close()

    body = client.get("/api/analytics/creators").json()
    by_id = {r["author_id"]: r for r in body}
    assert by_id["au1"]["author_name"] == "官方名"
    assert by_id["au1"]["follower_count"] == 12345 and by_id["au1"]["verified"] is True
    # 无档案作者回退 group-by 的名字，字段取默认
    assert by_id["au2"]["author_name"] == "无档案"
    assert by_id["au2"]["follower_count"] == 0 and by_id["au2"]["profile_id"] is None
