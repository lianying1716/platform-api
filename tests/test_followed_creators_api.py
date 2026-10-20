"""关注列表 API：list / refresh(upsert+回收) / 开关 / 立即同步 / 取消关注。

关键回归：同步开关只有一份状态（subscription），
所以「在订阅源里改/删」与「在关注列表里改」永远一致，不可能漂移。
"""

from datetime import datetime

import pytest

from app.models import AccountCredential, CreatorProfile, PlatformAccount, Subscription


def _account(session, platform="bilibili", name="主号"):
    acc = PlatformAccount(
        platform=platform, display_name=name, credential_kind="cookie", status="active"
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    from app.core.crypto import encrypt_credential

    session.add(
        AccountCredential(account_id=acc.id, ciphertext=encrypt_credential({"cookie": "c=1", "dedeuserid": "999"}))
    )
    session.commit()
    return acc


def _fake_followings(items):
    async def _list(credential, limit=200):
        return items

    return _list


def _author(author_id="1001", name="老饭骨", avatar="http://a/1.jpg", **kw):
    base = {
        "author_id": author_id, "author_name": name, "avatar": avatar,
        "home_url": f"https://space.bilibili.com/{author_id}", "signature": "吃饭",
        "verified": False, "follower_count": 100, "like_count": 5, "raw": {},
    }
    base.update(kw)
    return base


def test_refresh_upserts_and_lists(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(
        BilibiliAdapter, "list_followings",
        _fake_followings([_author("1001", "老饭骨"), _author("1002", "影视飓风", verified=True)]),
    )

    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2

    # 幂等：再次刷新不新增
    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r.json()["count"] == 2
    assert session.query(Subscription).filter_by(source_type="creator").count() == 2

    rows = client.get(f"/api/followed-creators?platform=bilibili&account_id={acc.id}").json()
    assert [c["author_name"] for c in rows] == ["老饭骨", "影视飓风"]
    assert rows[0]["avatar"] == "http://a/1.jpg"
    assert rows[0]["avatar_url"].startswith("/api/media/avatars/")
    assert rows[0]["home_url"] == "https://space.bilibili.com/1001"
    # 关注列表默认开启「关系跟踪」（对齐抖小云），但博主视频同步默认关闭，需手动开
    assert rows[0]["sync_enabled"] is True
    assert rows[0]["sync_videos"] is False


def test_refresh_writes_creator_profile(client, session, monkeypatch):
    """博主资料落在 creator_profile（与关系状态分表），不再有第二张关注表。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author()]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})

    p = session.query(CreatorProfile).filter_by(platform="bilibili", author_id="1001").one()
    assert p.author_name == "老饭骨"
    assert p.avatar == "http://a/1.jpg"
    assert p.follower_count == 100


def test_refresh_reflects_updated_avatar_and_keeps_flags(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author(avatar="old.jpg")]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    sub = session.query(Subscription).filter_by(source_type="creator").one()
    client.put(f"/api/followed-creators/{sub.id}", json={"sync_enabled": True, "full_sync": True})

    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author(avatar="new.jpg")]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id, "force": True})
    session.refresh(sub)
    p = session.query(CreatorProfile).filter_by(platform="bilibili", author_id="1001").one()
    assert p.avatar == "new.jpg"          # 资料更新
    assert sub.enabled is True and sub.only_new is False   # 开关保留


def test_refresh_persists_follower_count_for_douyin(client, session, monkeypatch):
    """抖音关注列表自带粉丝数：刷新必须落进 creator_profile 并由接口返回。

    回归：此前抖音适配器把 follower_count 写死 0，且抖音没有 fetch_follower_count，
    补全逻辑直接跳过，导致关注列表整页粉丝数永远显示「—」。
    """
    from app.modules.douyin import DouyinAdapter

    acc = _account(session, platform="douyin")
    monkeypatch.setattr(
        DouyinAdapter,
        "list_followings",
        _fake_followings(
            [
                _author("SEC1", "姜胡说", follower_count=4928841,
                        home_url="https://www.douyin.com/user/SEC1"),
                _author("SEC2", "无粉丝数据", follower_count=0,
                        home_url="https://www.douyin.com/user/SEC2"),
            ]
        ),
    )

    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r.status_code == 200, r.text

    p = session.query(CreatorProfile).filter_by(platform="douyin", author_id="SEC1").one()
    assert p.follower_count == 4928841

    rows = {c["author_id"]: c for c in client.get("/api/followed-creators?platform=douyin").json()}
    assert rows["SEC1"]["follower_count"] == 4928841
    assert rows["SEC2"]["follower_count"] == 0


def test_follower_count_falls_back_to_raw_snapshot(client, session):
    """历史行（列写 0、raw 快照有值）读取时要回退 raw，不必等下次刷新。"""
    from app.models import Subscription

    acc = _account(session, platform="douyin")
    session.add(
        Subscription(
            account_id=acc.id, platform="douyin", source_type="creator",
            remote_id="SEC9", title="旧数据博主", enabled=True,
        )
    )
    session.add(
        CreatorProfile(
            platform="douyin", author_id="SEC9", author_name="旧数据博主",
            follower_count=0, raw={"follower_count": 12345, "nickname": "旧数据博主"},
        )
    )
    session.commit()

    rows = client.get("/api/followed-creators?platform=douyin").json()
    assert rows[0]["follower_count"] == 12345


def test_toggle_writes_subscription_only(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author()]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    sub = session.query(Subscription).filter_by(source_type="creator").one()

    # 开启同步（只同步新发布）
    client.put(f"/api/followed-creators/{sub.id}", json={"sync_enabled": True, "full_sync": False})
    session.refresh(sub)
    assert sub.enabled is True and sub.only_new is True

    # 切全量
    client.put(f"/api/followed-creators/{sub.id}", json={"sync_enabled": True, "full_sync": True})
    session.refresh(sub)
    assert sub.only_new is False

    # 关闭同步
    client.put(f"/api/followed-creators/{sub.id}", json={"sync_enabled": False, "full_sync": True})
    session.refresh(sub)
    assert sub.enabled is False


# ------------------------------------------------------------------ 一致性回归


def test_no_drift_when_disabled_from_subscriptions_api(client, session, monkeypatch):
    """在「订阅源」接口禁用后，关注列表必须立刻反映（同一行数据）。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author()]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    sub = session.query(Subscription).filter_by(source_type="creator").one()
    client.put(f"/api/followed-creators/{sub.id}", json={"sync_enabled": True})

    # 从订阅源接口禁用
    body = next(s for s in client.get("/api/subscriptions").json() if s["id"] == sub.id)
    body = {k: body[k] for k in (
        "account_id", "source_type", "remote_id", "title", "enabled", "auto_download",
        "interval_minutes", "subdir_template", "min_play", "publish_after_days",
        "max_items", "only_new", "download_quality", "comment_mode")}
    body["enabled"] = False
    assert client.put(f"/api/subscriptions/{sub.id}", json=body).status_code == 200

    rows = client.get("/api/followed-creators").json()
    assert rows[0]["sync_enabled"] is False, "订阅源里禁用后，关注列表必须同步显示为关闭"


def test_no_drift_when_deleted_from_subscriptions_api(client, session, monkeypatch):
    """在「订阅源」删除后，关注列表不应再显示这条（没有幽灵状态）。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author()]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    sub = session.query(Subscription).filter_by(source_type="creator").one()
    assert client.delete(f"/api/subscriptions/{sub.id}").status_code == 200
    assert client.get("/api/followed-creators").json() == []


def test_refresh_does_not_touch_sync_switch(client, session, monkeypatch):
    """刷新**不能**擅自改同步开关。

    平台关注列表可能因分页/风控只返回一部分，若据此判"取关"并关同步，
    会把用户明明开着的同步误关掉。要停止跟踪必须显式移除。
    """
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author("1001"), _author("1002", "影视飓风")]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    sub1 = session.query(Subscription).filter_by(remote_id="1001").one()
    client.put(f"/api/followed-creators/{sub1.id}", json={"sync_enabled": True})

    # 平台上取关了 1001（本次列表里没有它）
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author("1002", "影视飓风")]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id, "force": True})
    session.refresh(sub1)
    assert sub1.enabled is True, "刷新不得关闭用户已开启的同步"
    assert session.query(Subscription).filter_by(remote_id="1001").count() == 1


def test_refresh_partial_page_keeps_existing_sync(client, session, monkeypatch):
    """平台分页只回一部分（真实常见）时，未出现的人也不能被关掉同步。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(
        BilibiliAdapter, "list_followings",
        _fake_followings([_author("1001"), _author("1002", "影视飓风"), _author("1003", "科技美学")]),
    )
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    for rid in ("1001", "1002", "1003"):
        s = session.query(Subscription).filter_by(remote_id=rid).one()
        client.put(f"/api/followed-creators/{s.id}", json={"sync_enabled": True})

    # 第二页没拉到，只回了第一个人
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author("1001")]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id, "force": True})
    session.expire_all()          # 读库里的真实状态，避免身份映射里的旧值
    enabled = {s.remote_id: s.enabled for s in session.query(Subscription).all()}
    assert enabled == {"1001": True, "1002": True, "1003": True}


def test_unfollow_soft_deletes_creator_subscription(client, session, monkeypatch):
    """取消关注 = 软删（保留订阅行 + 停用来源），不物理删除；与删订阅同一保留策略。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author()]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    sub_id = session.query(Subscription).filter_by(source_type="creator").one().id
    r = client.delete(f"/api/followed-creators/{sub_id}")
    assert r.status_code == 200
    session.expire_all()
    row = session.get(Subscription, sub_id)
    # 行仍在（保留溯源历史），但已软删且同步关闭
    assert row is not None and row.deleted_at is not None and row.enabled is False
    # 已不再出现在关注列表里
    listed = client.get("/api/followed-creators").json()
    assert all(x["id"] != sub_id for x in listed)


# ------------------------------------------------------------------ 通用错误路径


def test_refresh_requires_credential(client, session):
    acc = PlatformAccount(platform="bilibili", display_name="无凭证")
    session.add(acc)
    session.commit()
    session.refresh(acc)
    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r.status_code == 400


def test_refresh_unknown_account(client):
    assert client.post("/api/followed-creators/refresh", json={"account_id": 9999}).status_code == 404


def test_toggle_unknown_404(client):
    assert client.put("/api/followed-creators/9999", json={"sync_enabled": True}).status_code == 404


def test_toggle_rejects_non_creator_subscription(client, session):
    """收藏夹订阅不能从关注列表接口改开关（否则又是另一种越权漂移）。"""
    acc = _account(session)
    sub = Subscription(account_id=acc.id, platform="bilibili", source_type="favorite", remote_id="fav-1")
    session.add(sub)
    session.commit()
    assert client.put(f"/api/followed-creators/{sub.id}", json={"sync_enabled": True}).status_code == 404


def test_immediate_sync_enqueues_force_job(client, session):
    """API 只提交命令；不在 API 进程内跑平台抓取。"""
    from app.models import JobQueue

    acc = _account(session)
    sub = Subscription(
        account_id=acc.id,
        platform="bilibili",
        source_type="creator",
        remote_id="1001",
        title="老饭骨",
        enabled=False,
        sync_videos=False,
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)

    r = client.post(f"/api/followed-creators/{sub.id}/sync")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued" and body["job_id"] > 0

    session.expire_all()
    row = session.get(Subscription, sub.id)
    assert row.enabled is True and row.sync_videos is True
    assert row.last_sync_at is None, "同步尚未执行，不能提前写成功时间"
    job = session.get(JobQueue, body["job_id"])
    assert job.job_type == "sync" and job.payload == {
        "subscription_id": sub.id,
        "force": True,
    }


def test_immediate_sync_unknown_creator_404(client):
    assert client.post("/api/followed-creators/9999/sync").status_code == 404


def test_job_status_endpoint_resolves_sync_run(client, session):
    """提交后前端通过 job_id 查到 job 与对应 run，不靠猜接口返回值。"""
    from app.core.queue import enqueue
    from app.models import JobQueue, SyncRun

    acc = _account(session)
    sub = Subscription(
        account_id=acc.id,
        platform="bilibili",
        source_type="favorite",
        remote_id="fav-1",
        enabled=True,
    )
    session.add(sub)
    session.commit()
    job = enqueue(session, "sync", {"subscription_id": sub.id, "force": True})
    run = SyncRun(
        subscription_id=sub.id,
        job_id=job.id,
        status="success",
        added=2,
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
    )
    session.add(run)
    session.commit()

    data = client.get(f"/api/pipeline/jobs/{job.id}").json()
    assert data["status"] == "queued"
    assert data["run_id"] == run.id



# ---------------------------------------------------------------- 缓存 / 风控


def _counting_followings(items):
    calls = {"n": 0}

    async def _list(credential, limit=200):
        calls["n"] += 1
        return items

    return _list, calls


def test_refresh_uses_cache_second_time(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    lister, calls = _counting_followings([_author()])
    monkeypatch.setattr(BilibiliAdapter, "list_followings", lister)

    first = client.post("/api/followed-creators/refresh", json={"account_id": acc.id}).json()
    assert first["cached"] is False and calls["n"] == 1
    second = client.post("/api/followed-creators/refresh", json={"account_id": acc.id}).json()
    assert second["cached"] is True and calls["n"] == 1, "第二次应命中缓存，不再打平台"


def test_refresh_force_bypasses_cache(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    lister, calls = _counting_followings([_author()])
    monkeypatch.setattr(BilibiliAdapter, "list_followings", lister)
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id, "force": True})
    assert calls["n"] == 2


def test_sync_marks_from_creator_avatar_in_subscriptions(client, session, monkeypatch):
    """订阅列表里 creator 行带上博主头像（资料来自 creator_profile）。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author()]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    sub = session.query(Subscription).filter_by(source_type="creator").one()
    client.put(f"/api/followed-creators/{sub.id}", json={"sync_enabled": True})

    mine = next(s for s in client.get("/api/subscriptions").json() if s["id"] == sub.id)
    assert mine["creator_avatar"] == "http://a/1.jpg"


def test_reimport_revives_soft_deleted_creator(client, session, monkeypatch):
    """取消关注（软删）后平台侧仍在关注：刷新要复活该行，而不是因唯一约束永久消失。"""
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session)
    monkeypatch.setattr(BilibiliAdapter, "list_followings", _fake_followings([_author("1001")]))
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    sub_id = session.query(Subscription).filter_by(source_type="creator").one().id
    assert client.delete(f"/api/followed-creators/{sub_id}").status_code == 200
    session.expire_all()
    assert session.get(Subscription, sub_id).deleted_at is not None

    # 再次刷新：平台仍返回该博主 → 应复活同一行，而不是新建/消失
    client.post("/api/followed-creators/refresh", json={"account_id": acc.id, "force": True})
    session.expire_all()
    rows = session.query(Subscription).filter_by(source_type="creator").all()
    assert len(rows) == 1
    assert rows[0].id == sub_id and rows[0].deleted_at is None
    listed = client.get("/api/followed-creators").json()
    assert any(x["id"] == sub_id for x in listed)
