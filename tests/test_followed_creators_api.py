"""关注博主 API：list / refresh(upsert) / 开关 / 立即同步。全程 mock 适配器。"""

import pytest

from app.models import FollowedCreator, PlatformAccount, Subscription


def _account(session, platform="bilibili", name="主号"):
    acc = PlatformAccount(platform=platform, display_name=name, credential_kind="cookie")
    session.add(acc)
    session.commit()
    session.refresh(acc)
    # 需要一条凭证，refresh 才走得下去
    from app.core.crypto import encrypt_credential
    from app.models import AccountCredential

    session.add(
        AccountCredential(account_id=acc.id, ciphertext=encrypt_credential({"cookie": "c=1", "dedeuserid": "999"}))
    )
    session.commit()
    return acc


def _fake_followings(items):
    async def _list(credential, limit=200):
        return items

    return _list


def test_refresh_upserts_and_lists(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(
        BilibiliAdapter,
        "list_followings",
        _fake_followings(
            [
                {"author_id": "1001", "author_name": "老饭骨", "avatar": "http://a/1.jpg",
                 "home_url": "https://space.bilibili.com/1001", "signature": "吃饭",
                 "verified": False, "follower_count": 100, "like_count": 5, "raw": {}},
                {"author_id": "1002", "author_name": "影视飓风", "avatar": "http://a/2.jpg",
                 "home_url": "https://space.bilibili.com/1002", "signature": "影像",
                 "verified": True, "follower_count": 200, "like_count": 9, "raw": {}},
            ]
        ),
    )

    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2

    # 幂等：再次刷新不新增
    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r.json()["count"] == 2
    assert session.query(FollowedCreator).count() == 2

    rows = client.get(f"/api/followed-creators?platform=bilibili&account_id={acc.id}").json()
    assert [c["author_name"] for c in rows] == ["老饭骨", "影视飓风"]
    assert rows[0]["avatar"] == "http://a/1.jpg"
    assert rows[0]["home_url"] == "https://space.bilibili.com/1001"
    assert rows[0]["sync_enabled"] is False


def test_refresh_reflects_updated_avatar_and_keeps_flags(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(
        BilibiliAdapter, "list_followings",
        _fake_followings([{"author_id": "1001", "author_name": "老饭骨", "avatar": "old.jpg",
                           "home_url": "u", "signature": "", "verified": False,
                           "follower_count": 1, "like_count": 0, "raw": {}}]),
    )
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    fc = session.query(FollowedCreator).one()
    client.put(f"/api/followed-creators/{fc.id}", json={"sync_enabled": True, "full_sync": True})

    # 换头像后刷新，开关保留、头像更新
    monkeypatch.setattr(
        BilibiliAdapter, "list_followings",
        _fake_followings([{"author_id": "1001", "author_name": "老饭骨", "avatar": "new.jpg",
                           "home_url": "u", "signature": "", "verified": False,
                           "follower_count": 2, "like_count": 0, "raw": {}}]),
    )
    # force 跳过 6h 缓存，模拟「数据变了想重拉」
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id, "force": True})
    session.refresh(fc)
    assert fc.avatar == "new.jpg"
    assert fc.sync_enabled is True and fc.full_sync is True


def test_toggle_sync_creates_and_updates_subscription(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(
        BilibiliAdapter, "list_followings",
        _fake_followings([{"author_id": "1001", "author_name": "老饭骨", "avatar": "a",
                           "home_url": "u", "signature": "", "verified": False,
                           "follower_count": 0, "like_count": 0, "raw": {}}]),
    )
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    fc = session.query(FollowedCreator).one()

    # 开启同步（只同步新发布）→ 建 creator 订阅，only_new=True
    client.put(f"/api/followed-creators/{fc.id}", json={"sync_enabled": True, "full_sync": False})
    sub = session.query(Subscription).filter_by(source_type="creator", remote_id="1001").one()
    assert sub.enabled is True and sub.only_new is True

    # 切全量 → only_new=False
    client.put(f"/api/followed-creators/{fc.id}", json={"sync_enabled": True, "full_sync": True})
    session.refresh(sub)
    assert sub.only_new is False

    # 关闭同步 → 订阅禁用/删除
    client.put(f"/api/followed-creators/{fc.id}", json={"sync_enabled": False, "full_sync": True})
    remaining = session.query(Subscription).filter_by(source_type="creator", remote_id="1001").all()
    for s in remaining:
        session.refresh(s)
    assert all(s.enabled is False for s in remaining)


def test_refresh_requires_credential(client, session):
    acc = PlatformAccount(platform="bilibili", display_name="无凭证")
    session.add(acc)
    session.commit()
    session.refresh(acc)
    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r.status_code == 400


def test_refresh_unknown_account(client):
    r = client.post("/api/followed-creators/refresh", json={"account_id": 9999})
    assert r.status_code == 404


def test_toggle_unknown_404(client):
    assert client.put("/api/followed-creators/9999", json={"sync_enabled": True}).status_code == 404


def test_immediate_sync_runs_subscription(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter
    from app.services import sync_service

    acc = _account(session)
    monkeypatch.setattr(
        BilibiliAdapter, "list_followings",
        _fake_followings([{"author_id": "1001", "author_name": "老饭骨", "avatar": "a",
                           "home_url": "u", "signature": "", "verified": False,
                           "follower_count": 0, "like_count": 0, "raw": {}}]),
    )
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    fc = session.query(FollowedCreator).one()
    client.put(f"/api/followed-creators/{fc.id}", json={"sync_enabled": True, "full_sync": False})

    async def _fake_fetch(self, credential, source_type, remote_id, limit):
        return []

    monkeypatch.setattr(BilibiliAdapter, "fetch_source", _fake_fetch)
    r = client.post(f"/api/followed-creators/{fc.id}/sync")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    session.refresh(fc)
    assert fc.last_synced_at is not None


# ---------------------------------------------------------------- 缓存 / 风控


def _counting_followings(items):
    calls = {"n": 0}

    async def _list(credential, limit=200):
        calls["n"] += 1
        return items

    return _list, calls


_SAMPLE = [
    {"author_id": "1001", "author_name": "老饭骨", "avatar": "a", "home_url": "u",
     "signature": "", "verified": False, "follower_count": 0, "like_count": 0, "raw": {}},
]


def test_refresh_uses_cache_second_time(client, session, monkeypatch):
    """第二次刷新命中缓存：不再次调用适配器（0 网络请求）。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    lister, calls = _counting_followings(_SAMPLE)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", lister)

    r1 = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r1.status_code == 200 and r1.json()["count"] == 1
    assert calls["n"] == 1
    assert r1.json()["cached"] is False

    r2 = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r2.status_code == 200 and r2.json()["count"] == 1
    assert calls["n"] == 1, "第二次应命中缓存，不再打平台"
    assert r2.json()["cached"] is True


def test_refresh_force_bypasses_cache(client, session, monkeypatch):
    """force=true 跳过缓存强制拉取（仍受冷却限制）。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    lister, calls = _counting_followings(_SAMPLE)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", lister)
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id, "force": True})
    assert calls["n"] == 2


def test_refresh_cooldown_serves_cache_without_request(client, session, monkeypatch):
    """风控冷却期内：即使 force 也不打平台，返回缓存 + cooling 标记。"""
    from app.core import fetch_cache
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    lister, calls = _counting_followings(_SAMPLE)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", lister)
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert calls["n"] == 1

    fetch_cache.set_cooldown(session, f"cooldown:bilibili:{acc.id}:followings", seconds=600)
    session.commit()

    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id, "force": True})
    assert r.status_code == 200
    assert calls["n"] == 1, "冷却期内不得再打平台"
    assert r.json()["cooling"] is True


def test_refresh_risk_enters_cooldown(client, session, monkeypatch):
    """适配器抛风控异常时：写冷却 + 502 可读报错。"""
    import httpx

    from app.core import fetch_cache
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)

    async def _boom(credential, limit=200):
        raise RuntimeError("B站风控校验失败（-352），请稍后再试")

    monkeypatch.setattr(BilibiliAdapter, "list_followings", _boom)
    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r.status_code == 502
    assert "风控" in r.json()["detail"]
    assert fetch_cache.is_cooling(session, f"cooldown:bilibili:{acc.id}:followings")


def test_stats_enrichment_uses_24h_cache(client, session, monkeypatch):
    """粉丝数补全走 24h 缓存：第二次刷新不重复请求统计接口。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    lister, lcalls = _counting_followings(_SAMPLE)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", lister)
    stat_calls = {"n": 0}

    async def _stat(self, vmid, credential):
        stat_calls["n"] += 1
        return 12345

    monkeypatch.setattr(BilibiliAdapter, "_follower_count", _stat)

    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert stat_calls["n"] == 1
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id, "force": True})
    assert stat_calls["n"] == 1, "统计应命中 24h 缓存"
    fc = session.query(FollowedCreator).one()
    session.refresh(fc)
    assert fc.follower_count == 12345
