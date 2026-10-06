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
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
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
