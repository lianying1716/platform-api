"""收藏/点赞/关注列表同步分离（对齐抖小云）。

覆盖用户要求：
1. 收藏、点赞是各自独立的同步入口，可用账号级开关手动打开/关闭；
2. 关注列表默认开启「关系跟踪」（导入即生效），但博主名下视频必须手动开 sync_videos；
3. 手动开启后的链路真的生效：sync_videos 落库 → 同步可跑 → 结果可观测；
4. 全量更新（only_new=false）要能观测到「刷新了几条」（sync_run.updated）。
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import ContentItem, PlatformAccount, Subscription, SyncRun
from app.services import sync_service


def _account(session, platform="douyin", name="主号"):
    from app.core.crypto import encrypt_credential
    from app.models import AccountCredential

    acc = PlatformAccount(platform=platform, display_name=name, credential_kind="cookie", status="active")
    session.add(acc)
    session.flush()
    acc.credential = AccountCredential(
        ciphertext=encrypt_credential({"cookie": "c=1", "sec_user_id": "SEC1"})
    )
    session.commit()
    session.refresh(acc)
    return acc


def _creator_sub(session, acc, *, sync_videos=False, enabled=True):
    sub = Subscription(
        account_id=acc.id,
        platform=acc.platform,
        source_type="creator",
        remote_id="AUTHOR1",
        title="某博主",
        enabled=enabled,
        sync_videos=sync_videos,
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)
    return sub


def _patch_fetch(monkeypatch, platform, items):
    async def fake_fetch(self, credential, source_type, remote_id, limit):
        return list(items)

    monkeypatch.setattr(
        f"app.modules.{platform}.{platform.capitalize()}Adapter.fetch_source", fake_fetch, raising=False
    )


def _item(ext, **over):
    base = {"external_id": ext, "content_type": "video", "title": f"t-{ext}", "url": f"u/{ext}"}
    base.update(over)
    return base


# ------------------------------------------------ 关注列表：默认跟踪关系、视频需手动开


def test_refresh_enables_relation_but_not_video_sync(client, session, monkeypatch):
    from app.modules.bilibili import BilibiliAdapter

    acc = _account(session, platform="bilibili")

    async def _list(credential, limit=200):
        return [{"author_id": "1001", "author_name": "老饭骨", "avatar": "a.jpg"}]

    monkeypatch.setattr(BilibiliAdapter, "list_followings", _list)
    r = client.post("/api/followed-creators/refresh", json={"account_id": acc.id})
    assert r.status_code == 200, r.text

    rows = client.get("/api/followed-creators").json()
    assert rows[0]["sync_enabled"] is True, "关注列表默认同步（关系跟踪）"
    assert rows[0]["sync_videos"] is False, "博主名下视频必须手动开启"


def test_creator_video_sync_blocked_until_manually_opened(session, monkeypatch):
    """未开 sync_videos 时自动同步直接拒绝（worker/定时路径），避免"看着开着、其实没同步"。"""
    acc = _account(session)
    sub = _creator_sub(session, acc, sync_videos=False)

    _patch_fetch(monkeypatch, "douyin", [_item("V1", author_id="AUTHOR1")])
    run = asyncio.run(sync_service.run_subscription_sync(session, sub))

    assert run.status == "error"
    assert "同步博主视频" in run.error
    assert session.scalar(select(ContentItem)) is None


def test_manual_toggle_then_sync_ingests_videos(client, session, monkeypatch):
    """手动打开 sync_videos 后，链路真的生效：落库 + 同步入库。"""
    acc = _account(session)
    sub = _creator_sub(session, acc, sync_videos=False)

    # 打开二级开关：只有显式点这个按钮才会同步博主视频
    r = client.put(f"/api/followed-creators/{sub.id}", json={"sync_videos": True})
    assert r.status_code == 200, r.text
    assert r.json()["sync_videos"] is True

    session.expire_all()
    assert session.get(Subscription, sub.id).sync_videos is True, "开关必须真正写库"

    _patch_fetch(monkeypatch, "douyin", [_item("V1", author_id="AUTHOR1")])
    r = client.post(f"/api/followed-creators/{sub.id}/sync")
    body = r.json()
    assert body["status"] == "success", body
    assert body["added"] == 1
    assert session.scalar(select(ContentItem)).external_id == "V1"


def test_opening_video_sync_also_opens_relation(client, session):
    """打开视频同步时必须同时跟踪关注关系，否则同步会被拒。"""
    acc = _account(session)
    sub = _creator_sub(session, acc, sync_videos=False, enabled=False)

    client.put(f"/api/followed-creators/{sub.id}", json={"sync_videos": True})
    session.expire_all()
    row = session.get(Subscription, sub.id)
    assert row.sync_videos is True and row.enabled is True


def test_toggling_relation_does_not_clobber_video_switch(client, session):
    """两个开关是独立的：关关注关系不该顺手关掉视频同步开关本身。"""
    acc = _account(session)
    sub = _creator_sub(session, acc, sync_videos=True, enabled=True)

    client.put(f"/api/followed-creators/{sub.id}", json={"sync_enabled": False})
    session.expire_all()
    row = session.get(Subscription, sub.id)
    assert row.enabled is False and row.sync_videos is True


# -------------------------------------------------------------- 全量更新可观测


def _favorite_sub(session, acc, **kw):
    """收藏订阅：不走 creator 的 15 分钟缓存，用来干净地验证 only_new 刷新路径。"""
    kw.setdefault("enabled", True)
    sub = Subscription(
        account_id=acc.id, platform=acc.platform, source_type="favorite",
        remote_id="SEC1", title="我的收藏", **kw,
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)
    return sub


def test_full_sync_reports_updated_count(session, monkeypatch):
    """only_new=false 时已有内容被刷新 → sync_run.updated 要能看见。"""
    acc = _account(session)
    sub = _favorite_sub(session, acc, only_new=False)

    _patch_fetch(monkeypatch, "douyin", [_item("V1", title="首版", author_id="AUTHOR1")])
    run1 = asyncio.run(sync_service.run_subscription_sync(session, sub))
    assert run1.added == 1 and run1.updated == 0

    _patch_fetch(monkeypatch, "douyin", [_item("V1", title="改过标题", author_id="AUTHOR1")])
    run2 = asyncio.run(sync_service.run_subscription_sync(session, sub))
    assert run2.status == "success"
    assert run2.added == 0
    assert run2.updated == 1, "全量同步必须记录刷新了几条，否则看不出是否生效"
    session.expire_all()
    assert session.scalar(select(ContentItem)).title == "改过标题"


def test_only_new_true_does_not_report_updated(session, monkeypatch):
    acc = _account(session)
    sub = _favorite_sub(session, acc)  # only_new 默认 True

    _patch_fetch(monkeypatch, "douyin", [_item("V1", title="首版", author_id="AUTHOR1")])
    asyncio.run(sync_service.run_subscription_sync(session, sub))
    _patch_fetch(monkeypatch, "douyin", [_item("V1", title="改过标题", author_id="AUTHOR1")])
    run2 = asyncio.run(sync_service.run_subscription_sync(session, sub))
    assert run2.added == 0 and run2.updated == 0


# ------------------------------------------------------ 收藏 / 点赞账号级独立开关


def test_switch_list_shows_independent_switches(client, session):
    acc = _account(session)
    data = client.get(f"/api/accounts/{acc.id}/sync-switches").json()
    keys = {s["key"]: s for s in data["switches"]}
    # 点赞同步已删除；收藏 + 关注列表各自独立
    assert set(keys) == {"favorite", "creator"}
    # 收藏默认关闭，必须手动打开；关注列表还没有内容
    assert keys["favorite"]["enabled"] is False
    assert keys["creator"]["enabled"] is False   # 还没有关注列表


def test_toggle_favorite_is_independent_and_like_source_removed(client, session):
    acc = _account(session)

    r = client.put(f"/api/accounts/{acc.id}/sync-switches/favorite", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True

    data = client.get(f"/api/accounts/{acc.id}/sync-switches").json()
    keys = {s["key"]: s for s in data["switches"]}
    assert keys["favorite"]["enabled"] is True
    # 点赞同步已整条删除：开关列表里不再有 like
    assert "like" not in keys

    # 落库检查：收藏是一条 enabled=True 的 favorite 订阅，没有任何 like 订阅
    favs = session.scalars(select(Subscription).where(Subscription.source_type == "favorite")).all()
    assert len(favs) == 1 and favs[0].enabled is True
    assert session.scalars(select(Subscription).where(Subscription.source_type == "like")).all() == []


def test_toggle_favorite_off_then_on_reuses_same_row(client, session):
    acc = _account(session)
    client.put(f"/api/accounts/{acc.id}/sync-switches/favorite", json={"enabled": True})
    sub_id = session.scalars(select(Subscription).where(Subscription.source_type == "favorite")).one().id
    client.put(f"/api/accounts/{acc.id}/sync-switches/favorite", json={"enabled": False})
    session.expire_all()
    row = session.get(Subscription, sub_id)
    assert row.enabled is False

    # 再打开：复用同一行，不因唯一约束冲突
    client.put(f"/api/accounts/{acc.id}/sync-switches/favorite", json={"enabled": True})
    session.expire_all()
    rows = session.scalars(select(Subscription).where(Subscription.source_type == "favorite")).all()
    assert len(rows) == 1 and rows[0].enabled is True


def test_bilibili_has_watch_later_but_no_account_level_favorite_switch(client, session):
    acc = _account(session, platform="bilibili")
    data = client.get(f"/api/accounts/{acc.id}/sync-switches").json()
    keys = {s["key"] for s in data["switches"]}
    # B站没有账号级收藏单例源（收藏夹是多个命名源），但「稍后再看」是账号级单例。
    assert keys == {"creator", "watch_later"}

    r = client.put(f"/api/accounts/{acc.id}/sync-switches/favorite", json={"enabled": True})
    assert r.status_code == 400


def test_create_subscription_defaults_closed_for_favorite(client, session):
    """收藏订阅在未显式指定 enabled 时默认关闭（需手动打开）。"""
    acc = _account(session)
    r = client.post(
        "/api/subscriptions",
        json={"account_id": acc.id, "source_type": "favorite", "remote_id": "fav1", "title": "收藏"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False


def test_create_creator_subscription_defaults_open_relation(client, session):
    acc = _account(session)
    r = client.post(
        "/api/subscriptions",
        json={"account_id": acc.id, "source_type": "creator", "remote_id": "A1", "title": "博主"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    assert r.json()["sync_videos"] is False


def test_partial_put_does_not_reset_switches(client, session):
    """PUT 是全字段覆盖：未显式带来的开关字段不能被重置。"""
    acc = _account(session)
    sub = _creator_sub(session, acc, sync_videos=True, enabled=True)
    sub.only_new = False
    session.commit()

    # 只改标题，且不带 enabled / only_new / sync_videos
    body = {
        "account_id": acc.id,
        "source_type": "creator",
        "remote_id": "AUTHOR1",
        "title": "改了标题",
    }
    r = client.put(f"/api/subscriptions/{sub.id}", json=body)
    assert r.status_code == 200, r.text
    session.expire_all()
    row = session.get(Subscription, sub.id)
    assert row.title == "改了标题"
    assert row.enabled is True and row.sync_videos is True and row.only_new is False


def test_runs_endpoint_exposes_updated(client, session):
    acc = _account(session)
    sub = _creator_sub(session, acc, sync_videos=True)
    session.add(
        SyncRun(
            subscription_id=sub.id, status="success", fetched=5, added=1, updated=3, error=""
        )
    )
    session.commit()
    rows = client.get(f"/api/subscriptions/{sub.id}/runs").json()
    assert rows[0]["updated"] == 3


# ---------------------------------------------------- 定时扫描：开启后真的会被同步


def test_scheduler_enqueues_due_enabled_subscriptions(session, monkeypatch):
    """开启的收藏订阅到点后由 scheduler 入队 sync（"开启即周期同步"闭环）。"""
    import app.core.scheduler as sched
    from app.models import JobQueue

    monkeypatch.setattr(sched, "SessionLocal", lambda: session)
    acc = _account(session)
    _favorite_sub(session, acc, only_new=True)

    asyncio.run(sched.enqueue_due_subscriptions())
    jobs = session.scalars(select(JobQueue).where(JobQueue.job_type == "sync")).all()
    assert len(jobs) == 1
    assert jobs[0].payload["subscription_id"] is not None


def test_scheduler_skips_disabled_and_creator_without_video_sync(session, monkeypatch):
    import app.core.scheduler as sched
    from app.models import JobQueue

    monkeypatch.setattr(sched, "SessionLocal", lambda: session)
    acc = _account(session)
    _creator_sub(session, acc, sync_videos=False, enabled=True)   # 只跟踪关系 → 不入队
    _favorite_sub(session, acc, enabled=False)                    # 关闭 → 不入队

    asyncio.run(sched.enqueue_due_subscriptions())
    assert session.scalars(select(JobQueue).where(JobQueue.job_type == "sync")).all() == []


def test_scheduler_enqueues_creator_when_video_sync_open(session, monkeypatch):
    import app.core.scheduler as sched
    from app.models import JobQueue

    monkeypatch.setattr(sched, "SessionLocal", lambda: session)
    acc = _account(session)
    _creator_sub(session, acc, sync_videos=True, enabled=True)

    asyncio.run(sched.enqueue_due_subscriptions())
    assert len(session.scalars(select(JobQueue).where(JobQueue.job_type == "sync")).all()) == 1


def test_scheduler_respects_interval_minutes(session, monkeypatch):
    """刚同步过的订阅不到期不再入队。"""
    import app.core.scheduler as sched
    from datetime import datetime
    from app.models import JobQueue

    monkeypatch.setattr(sched, "SessionLocal", lambda: session)
    acc = _account(session)
    sub = _favorite_sub(session, acc)
    sub.interval_minutes = 360
    sub.last_sync_at = datetime.utcnow()
    session.commit()

    asyncio.run(sched.enqueue_due_subscriptions())
    assert session.scalars(select(JobQueue).where(JobQueue.job_type == "sync")).all() == []


def test_failed_sync_backs_off_instead_of_hammering(session, monkeypatch):
    """失败也要退避：last_attempt_at 更新后，同一 interval 内不再入队。

    回归背景：此前只用 last_sync_at（仅成功时更新）判到期，持续失败的订阅
    被每 5 分钟扫描重新入队 → 在平台风控期反复打平台。
    """
    import app.core.scheduler as sched
    from app.models import JobQueue

    monkeypatch.setattr(sched, "SessionLocal", lambda: session)
    acc = _account(session)
    sub = _favorite_sub(session, acc)
    sub.interval_minutes = 360
    sub.last_sync_at = None                 # 从未成功过
    sub.last_attempt_at = datetime.utcnow()  # 刚刚尝试过（且失败）
    session.commit()

    asyncio.run(sched.enqueue_due_subscriptions())
    assert session.scalars(select(JobQueue).where(JobQueue.job_type == "sync")).all() == [], \
        "刚尝试过的订阅不应立刻重排"


def test_sync_records_attempt_even_on_failure(session, monkeypatch):
    """run_subscription_sync 无论成败都要写 last_attempt_at。"""
    acc = _account(session)
    sub = _favorite_sub(session, acc)
    sub.last_attempt_at = None
    session.commit()

    # 账号不可用 → 必然失败
    acc2 = session.get(type(acc), acc.id)
    acc2.status = "deleted"
    session.commit()

    run = asyncio.run(sync_service.run_subscription_sync(session, sub))
    assert run.status == "error"
    session.expire_all()
    assert session.get(Subscription, sub.id).last_attempt_at is not None, \
        "失败也要记尝试时间，调度器才能退避"


def test_stale_attempt_after_interval_is_due(session, monkeypatch):
    """超过 interval 后即使一直失败，也应该再试一次（不是永久停摆）。"""
    import app.core.scheduler as sched
    from app.models import JobQueue

    monkeypatch.setattr(sched, "SessionLocal", lambda: session)
    acc = _account(session)
    sub = _favorite_sub(session, acc)
    sub.interval_minutes = 60
    sub.last_sync_at = None
    sub.last_attempt_at = datetime.utcnow() - timedelta(hours=2)
    session.commit()

    asyncio.run(sched.enqueue_due_subscriptions())
    assert len(session.scalars(select(JobQueue).where(JobQueue.job_type == "sync")).all()) == 1


# ---------------------------------------------------------------------------
# 账号级「同步总开关」= 沉默期（需求：关闭即禁止该账号所有同步/调用）
# ---------------------------------------------------------------------------
def test_master_switch_off_blocks_scheduler_enqueue(client, session):
    """总开关关闭后，即便订阅 enabled=True、账号 status=active，也不得排队。"""
    from app.core.scheduler import enqueue_due_subscriptions
    from app.models import JobQueue

    acc = _account(session)
    _creator_sub(session, acc, sync_videos=True, enabled=True)
    # 先打开一次确认正常会排队
    acc.sync_enabled = False
    session.commit()

    asyncio.run(enqueue_due_subscriptions())
    queued = session.scalars(select(JobQueue).where(JobQueue.job_type == "sync")).all()
    assert queued == [], "沉默期账号不允许排队同步"


def test_master_switch_off_rejects_manual_sync(session):
    """沉默期账号：手动同步被源头拒绝并如实记录错误。"""
    acc = _account(session)
    acc.sync_enabled = False
    session.commit()
    sub = _creator_sub(session, acc, sync_videos=True, enabled=True)
    run = asyncio.run(sync_service.run_subscription_sync(session, sub))
    assert run.status == "error"
    assert "沉默期" in (run.error or "")


def test_master_switch_off_allows_manual_check(client, session, monkeypatch):
    """沉默期账号：**手动**体检放行（用户后来明确要求"可以"）。

    原规则是"沉默期禁止一切平台调用，含体检"，一刀切。但实测发现两个漏洞叠加：
    自动巡检跳过沉默期 + 手动体检被 409 堵死 => 沉默期账号的假"在线"永远没人发现，
    开关一打开就报一堆错。现在改成：自动巡检仍跳过（不替用户发请求），手动体检放行。
    """
    from app.modules.base import CheckResult

    acc = _account(session)
    acc.sync_enabled = False
    session.commit()

    # 体检要真去平台，这里 mock 掉，避免测试打真接口。
    async def _ok(self, credential):
        return CheckResult(ok=True, message="在线")

    cls = __import__(f"app.modules.{acc.platform}", fromlist=["x"]).__dict__[
        {"douyin": "DouyinAdapter", "bilibili": "BilibiliAdapter"}.get(acc.platform, "")
    ] if acc.platform in ("douyin", "bilibili") else None
    if cls is not None:
        monkeypatch.setattr(cls, "check", _ok)

    r = client.post(f"/api/accounts/{acc.id}/check")
    assert r.status_code == 200, f"沉默期手动体检应放行：{r.status_code} {r.text}"


def test_master_switch_toggle_roundtrip(client, session):
    """总开关可开可关，落库并以 sync_enabled 回显。"""
    acc = _account(session)
    assert client.get("/api/accounts").json()[0]["sync_enabled"] is True

    r = client.put(f"/api/accounts/{acc.id}/sync-enabled", json={"enabled": False})
    assert r.status_code == 200 and r.json()["sync_enabled"] is False
    session.expire_all()
    assert session.get(PlatformAccount, acc.id).sync_enabled is False

    r = client.put(f"/api/accounts/{acc.id}/sync-enabled", json={"enabled": True})
    assert r.json()["sync_enabled"] is True
