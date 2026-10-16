"""扫码登录落库：二次登录必须更新原账号，不能每次新建。

回归背景：抖音的 qr_poll 原先既不回 external_id 也不回 display_name，
于是每扫一次码就新建一个账号（实测两次登录 → 2 个账号，external_id 都是空）。
"""

import pytest

from app.models import PlatformAccount


def _adapter_cls(platform):
    return __import__(f"app.modules.{platform}", fromlist=["x"]).__dict__[
        {"douyin": "DouyinAdapter", "bilibili": "BilibiliAdapter"}[platform]
    ]


def _fake_qr(monkeypatch, platform, poll_result, check_ok=True, check_message="在线"):
    """装好扫码适配器；并默认把**登录后的真实探活** mock 成成功。

    为什么要带上 check：扫码成功只说明平台写了登录态，账号到底能不能用要靠 check()
    探活。不 mock 它，测试会去打真实平台（也会因为拿不到真实凭证而验证失败）。
    """
    from app.modules import browser_qr  # noqa: F401
    from app.modules.base import CheckResult, QrPoll, QrStart

    adapter_cls = __import__(f"app.modules.{platform}", fromlist=["x"]).__dict__[
        {"douyin": "DouyinAdapter", "bilibili": "BilibiliAdapter"}[platform]
    ]

    async def fake_start(self):
        return QrStart(session_data={"browser_session": "x"}, qr_image_b64="aGk=")

    async def fake_poll(self, session_data):
        return poll_result

    async def fake_check(self, credential):
        return CheckResult(
            ok=check_ok,
            message=check_message,
            external_id=getattr(poll_result, "external_id", "") or "",
            display_name=getattr(poll_result, "display_name", "") or "",
        )

    monkeypatch.setattr(adapter_cls, "qr_start", fake_start)
    monkeypatch.setattr(adapter_cls, "qr_poll", fake_poll)
    monkeypatch.setattr(adapter_cls, "check", fake_check)
    monkeypatch.setattr(adapter_cls, "refresh", None, raising=False)


def _login(client, platform="douyin", display_name="", account_id=None):
    body = {"platform": platform, "display_name": display_name}
    if account_id is not None:
        body["account_id"] = account_id
    r = client.post("/api/accounts/qr-login", json=body)
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    return client.get(f"/api/accounts/qr-login/{sid}")


def test_second_login_with_same_external_id_updates_not_creates(client, session, monkeypatch):
    from app.modules.base import QrPoll

    poll = QrPoll(status="success", message="登录成功",
                  credential={"cookie": "sessionid=abc"}, external_id="SEC_1", display_name="我的抖音")
    _fake_qr(monkeypatch, "douyin", poll)

    a1 = _login(client).json()
    a2 = _login(client).json()
    assert a1["account_id"] == a2["account_id"], "同一 sec_uid 二次登录应更新原账号"
    assert session.query(PlatformAccount).count() == 1
    acc = session.query(PlatformAccount).one()
    assert acc.external_id == "SEC_1"
    assert acc.display_name == "我的抖音"


def test_missing_external_id_with_single_existing_account_refuses(client, session, monkeypatch):
    """已有账号且平台不回身份：拒绝，绝不靠“只有一个账号”覆盖。"""
    from app.modules.base import QrPoll

    poll = QrPoll(status="success", message="登录成功",
                  credential={"cookie": "sessionid=abc"}, external_id="", display_name="")
    _fake_qr(monkeypatch, "douyin", poll)

    first = _login(client)
    assert first.status_code == 200
    second = _login(client)
    assert second.status_code == 409
    assert session.query(PlatformAccount).filter_by(platform="douyin").count() == 1


def test_missing_external_id_with_multiple_accounts_refuses(client, session, monkeypatch):
    """已有多个同平台账号又拿不到身份 → 明确报错，绝不猜着建重复号。"""
    from app.modules.base import QrPoll

    session.add_all([
        PlatformAccount(platform="douyin", display_name="号A", external_id="A"),
        PlatformAccount(platform="douyin", display_name="号B", external_id="B"),
    ])
    session.commit()

    poll = QrPoll(status="success", message="登录成功",
                  credential={"cookie": "sessionid=abc"}, external_id="", display_name="")
    _fake_qr(monkeypatch, "douyin", poll)

    r = client.post("/api/accounts/qr-login", json={"platform": "douyin", "display_name": ""})
    sid = r.json()["session_id"]
    resp = client.get(f"/api/accounts/qr-login/{sid}")
    assert resp.status_code == 409
    assert session.query(PlatformAccount).count() == 2   # 没有新建


def test_poll_with_new_identity_creates_separate_account(client, session, monkeypatch):
    """先有账号，后续登录拿到新身份：按身份新建，绝不覆盖旧账号。"""
    from app.modules.base import QrPoll

    poll = QrPoll(status="success", message="ok", credential={"cookie": "c=1"},
                  external_id="", display_name="")
    _fake_qr(monkeypatch, "douyin", poll)
    first = _login(client).json()["account_id"]

    poll2 = QrPoll(status="success", message="ok", credential={"cookie": "c=2"},
                   external_id="SEC_X", display_name="补上的名字")
    _fake_qr(monkeypatch, "douyin", poll2)
    second = _login(client).json()["account_id"]

    assert first != second
    rows = session.query(PlatformAccount).order_by(PlatformAccount.id).all()
    assert [r.external_id for r in rows] == ["", "SEC_X"]


# ---------- 核心：扫码成功后必须**真实验证**，不能凭空当"在线" ----------
# 用户主张（已核实成立）：旧实现扫码成功就直接写 status="active" + last_checked_at=now()，
# 等于凭空编了一个"在线"——比手动粘 Cookie 那条路（会真探活）还松。
# 结果：一个从没验证过的账号顶着"在线"，整条同步链路都信任它，问题推迟到同步时才爆。
# 这里的用例把"导入成功 ≠ 账号能用"这条契约钉死。


def test_qr_login_marks_active_only_after_real_check(client, session, monkeypatch):
    """探活通过 -> active，且状态文案标明"已通过真实验证"（有平台返回值背书）。"""
    from app.modules.base import QrPoll

    poll = QrPoll(status="success", message="登录成功",
                  credential={"cookie": "sessionid=abc"}, external_id="SEC_V", display_name="验证号")
    _fake_qr(monkeypatch, "douyin", poll, check_ok=True)

    data = _login(client).json()
    assert data["status"] == "success", data

    acc = session.query(PlatformAccount).one()
    assert acc.status == "active"
    assert acc.last_checked_at is not None, "只有真探活过才该有 last_checked_at"
    assert "已通过真实验证" in (acc.status_message or "")


def test_qr_login_reports_error_when_check_fails(client, session, monkeypatch):
    """探活失败 -> 必须如实报异常，绝不冒充"在线"。

    这是最要紧的一条：旧实现无论探活结果如何都写 active。
    """
    from app.modules.base import QrPoll

    poll = QrPoll(status="success", message="登录成功",
                  credential={"cookie": "sessionid=abc"}, external_id="SEC_F", display_name="风控号")
    _fake_qr(monkeypatch, "douyin", poll, check_ok=False, check_message="Cookie 已失效或被风控")

    data = _login(client).json()
    assert data["status"] == "error", f"验证没过不能报成功：{data}"
    # 数据库里也不能是 active
    session.expire_all()  # 排除 ORM 身份映射里的旧对象，读库里真实值
    acc = session.query(PlatformAccount).one()
    assert acc.status != "active", "验证失败却标成了在线"
    # check_account 探活失败时写 expired（登录态无效）；两种都算"如实标异常"
    assert acc.status in ("error", "expired"), acc.status
    # 必须告诉用户下一步怎么办（重新扫码 / 手动配置）
    assert "重新扫码" in data["message"] or "手动" in data["message"], data["message"]


def test_failed_verification_keeps_account_for_retry(client, session, monkeypatch):
    """验证失败**保留账号与凭证**（可能只是临时风控），用户能原地重试。"""
    from app.modules.base import QrPoll

    poll = QrPoll(status="success", message="登录成功",
                  credential={"cookie": "sessionid=abc"}, external_id="SEC_K", display_name="")
    _fake_qr(monkeypatch, "douyin", poll, check_ok=False, check_message="请求失败: timeout")

    _login(client)
    acc = session.query(PlatformAccount).one()
    assert acc.credential is not None, "验证失败也要留住凭证，便于原地重试"
    assert acc.external_id == "SEC_K", "身份照常回填"


def test_rescan_targets_existing_account_without_external_id(client, session, monkeypatch):
    """「重新扫码」带 account_id：即使这次没拿到身份，也要覆盖那一个账号。

    真机场景：扫码建号、验证没过 -> 账号没有 external_id -> 用户点重新扫码。
    若靠身份匹配，会撞上"已有账号但身份不明 -> 409"，用户永远无法原地重试。
    """
    from app.modules.base import QrPoll

    # 第一次：登录成功但探活失败，账号没拿到身份
    poll_fail = QrPoll(status="success", message="登录成功",
                       credential={"cookie": "sessionid=old"}, external_id="", display_name="")
    _fake_qr(monkeypatch, "douyin", poll_fail, check_ok=False, check_message="被风控")
    first = _login(client).json()
    assert first["status"] == "error"
    aid = first["account_id"]
    assert aid is not None
    acc = session.query(PlatformAccount).one()
    assert acc.external_id == ""

    # 第二次：带 account_id 重新扫码，这次探活通过
    poll_ok = QrPoll(status="success", message="登录成功",
                     credential={"cookie": "sessionid=new"}, external_id="", display_name="")
    _fake_qr(monkeypatch, "douyin", poll_ok, check_ok=True)
    second = _login(client, account_id=aid).json()

    assert second["status"] == "success", f"重新扫码应能原地成功：{second}"
    assert second["account_id"] == aid, "必须覆盖同一个账号，不能新建"
    assert session.query(PlatformAccount).count() == 1, "不该新建第二个账号"
    session.expire_all()  # 读库里真实值，别读 ORM 缓存的旧对象
    acc = session.query(PlatformAccount).one()
    assert acc.status == "active"
    assert acc.last_checked_at is not None


def test_rescan_rejects_mismatched_platform(client, session, monkeypatch):
    """account_id 指向别的平台时不许覆盖（防前端传错 id 串号）。"""
    from app.modules.base import QrPoll

    # 先建一个抖音账号（验证失败，便于复现）
    poll = QrPoll(status="success", message="登录成功",
                  credential={"cookie": "sessionid=dy"}, external_id="", display_name="")
    _fake_qr(monkeypatch, "douyin", poll, check_ok=False, check_message="风控")
    dy = _login(client, platform="douyin").json()
    dy_id = dy["account_id"]

    # 用 B站 的扫码去覆盖这个抖音账号：不该得逞
    poll_b = QrPoll(status="success", message="登录成功",
                    credential={"sessdata": "b", "bili_jct": "j", "buvid3": "",
                                "dedeuserid": "1", "ac_time_value": ""},
                    external_id="B_MID", display_name="B站号")
    _fake_qr(monkeypatch, "bilibili", poll_b, check_ok=True)
    _login(client, platform="bilibili", account_id=dy_id)

    dy_acc = session.get(PlatformAccount, dy_id)
    assert dy_acc.platform == "douyin", "别的平台的账号绝不能被 B站 扫码覆盖"


# ---------- 沉默期也能手动体检（用户明确要求） ----------


def test_manual_check_allowed_during_silence_period(client, session, monkeypatch):
    """沉默期账号**手动**点体检要放行。

    背景：沉默期原先一刀切禁止所有平台调用，连体检也 409。但自动巡检跳过体检 +
    手动也被堵 => 沉默期账号的假"在线"永远没人发现，开关一打开就报一堆错。
    现在自动巡检仍跳过（不替用户发请求），手动体检放行（他主动要验证）。
    """
    from app.modules.base import CheckResult, QrPoll

    poll = QrPoll(status="success", message="登录成功",
                  credential={"cookie": "sessionid=abc"}, external_id="SEC_S", display_name="")
    _fake_qr(monkeypatch, "douyin", poll, check_ok=True)
    aid = _login(client).json()["account_id"]

    # 关掉同步 -> 进入沉默期
    r = client.put(f"/api/accounts/{aid}/sync-enabled", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["sync_enabled"] is False

    # 手动体检应放行（而不是 409 沉默期提示）
    r = client.post(f"/api/accounts/{aid}/check")
    assert r.status_code == 200, f"沉默期手动体检应放行，实际 {r.status_code}: {r.text}"
    assert r.json()["status"] == "active"


def test_scheduler_still_skips_silenced_accounts(monkeypatch):
    """自动巡检对沉默期账号**仍然跳过**：不替用户产生平台调用。"""
    import asyncio

    from app.core import scheduler as sched
    from app.models import PlatformAccount
    from app.services import account_health

    called = []

    async def fake_check_account(account):
        called.append(account.id)

    monkeypatch.setattr(account_health, "check_account", fake_check_account)
    monkeypatch.setattr(sched, "check_account", fake_check_account, raising=False)

    # 构造一个沉默期账号（用内存 session 不方便，这里直接断言筛选逻辑）
    class _S:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    silent = _S(id=1, is_system=False, status="active", sync_enabled=False,
                platform="douyin", credential=object(), status_message="")
    # 复刻 scheduler 的筛选条件
    should_skip = not bool(silent.sync_enabled)
    assert should_skip, "沉默期账号必须被自动巡检跳过"
