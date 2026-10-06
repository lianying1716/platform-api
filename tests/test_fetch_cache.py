"""抓取缓存 + 风控冷却测试（不联网）。"""

from datetime import datetime, timedelta

import pytest

from app.core import fetch_cache


def test_set_get_roundtrip(session):
    fetch_cache.set_cache(session, "k1", {"a": 1}, ttl_seconds=3600)
    session.commit()
    assert fetch_cache.get_cache(session, "k1") == {"a": 1}


def test_get_missing_returns_none(session):
    assert fetch_cache.get_cache(session, "nope") is None


def test_get_expired_returns_none(session):
    fetch_cache.set_cache(session, "k2", {"a": 1}, ttl_seconds=1)
    session.commit()
    # 手动把过期时间推到过去
    from app.models import FetchCache

    row = session.get(FetchCache, "k2")
    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    session.commit()
    assert fetch_cache.get_cache(session, "k2") is None


def test_set_overwrites(session):
    fetch_cache.set_cache(session, "k3", {"v": 1}, ttl_seconds=3600)
    session.commit()
    fetch_cache.set_cache(session, "k3", {"v": 2}, ttl_seconds=3600)
    session.commit()
    assert fetch_cache.get_cache(session, "k3") == {"v": 2}


def test_cooldown_set_and_active(session):
    assert fetch_cache.is_cooling(session, "cooldown:bilibili:1:followings") is False
    fetch_cache.set_cooldown(session, "cooldown:bilibili:1:followings", seconds=600)
    session.commit()
    assert fetch_cache.is_cooling(session, "cooldown:bilibili:1:followings") is True
    remain = fetch_cache.cooldown_remaining(session, "cooldown:bilibili:1:followings")
    assert 0 < remain <= 600


def test_cooldown_expired(session):
    fetch_cache.set_cooldown(session, "cooldown:x", seconds=1)
    session.commit()
    from app.models import FetchCache

    row = session.get(FetchCache, "cooldown:x")
    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    session.commit()
    assert fetch_cache.is_cooling(session, "cooldown:x") is False


def test_purge_expired_removes_rows(session):
    fetch_cache.set_cache(session, "live", {"v": 1}, ttl_seconds=3600)
    fetch_cache.set_cache(session, "dead", {"v": 1}, ttl_seconds=1)
    session.commit()
    from app.models import FetchCache

    session.get(FetchCache, "dead").expires_at = datetime.utcnow() - timedelta(seconds=1)
    session.commit()
    removed = fetch_cache.purge_expired(session)
    session.commit()
    assert removed >= 1
    assert fetch_cache.get_cache(session, "live") == {"v": 1}


def test_parse_risk_signals():
    """风控识别：文本/异常内容里出现 412/352/-352 或关键字 → 判定为风控。"""
    assert fetch_cache.looks_like_risk("风控校验失败") is True
    assert fetch_cache.looks_like_risk("code=-352") is True
    assert fetch_cache.looks_like_risk("HTTP 412 Precondition Failed") is True
    assert fetch_cache.looks_like_risk("Blocked by ArgusSecurityPlugin") is True
    assert fetch_cache.looks_like_risk("普通网络错误") is False


def test_ttl_constants_sane():
    # 关注列表 ≥ 1h（不要频繁拉），投稿 ≥ 5min，统计 ≥ 6h
    assert fetch_cache.TTL_FOLLOWINGS >= 3600
    assert fetch_cache.TTL_CREATOR_VIDEOS >= 300
    assert fetch_cache.TTL_STATS >= 21600
    assert fetch_cache.COOLDOWN_SECONDS >= 300


@pytest.mark.asyncio
async def test_single_flight_coalesces():
    """同 key 的并发刷新应合并为一次真实执行（只调用底层一次）。"""
    calls = {"n": 0}

    async def work():
        calls["n"] += 1
        await __import__("asyncio").sleep(0.05)
        return {"n": calls["n"]}

    import asyncio

    r = await asyncio.gather(
        fetch_cache.single_flight("same", work),
        fetch_cache.single_flight("same", work),
        fetch_cache.single_flight("same", work),
    )
    assert calls["n"] == 1
    assert all(x == {"n": 1} for x in r)
