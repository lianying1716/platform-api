"""浏览器扫码轮询的状态流转（用假 page/context，不启动真实浏览器）。

回归背景：抖音扫码登录"扫了没反应"——
- 成功判定只看 LOGIN_STATUS cookie，而抖音 PC 端主要写 localStorage.HasUserLogin，
  导致扫完码仍判为未登录、永远停在 pending；
- 轮询从不返回 scanned，用户扫码后没有任何反馈；
- 前端轮询 catch{} 静默停掉，超时也不提示。
"""
import asyncio
import time

import pytest
from app.modules import browser_qr


class FakePage:
    """最小假页面：localStorage 读取 与 self-profile 请求 分别应答。"""
    def __init__(self, ls=None, url="https://www.douyin.com/jingxuan", profile=None):
        self._ls = ls or {}
        self._profile = profile
        self.url = url
    async def evaluate(self, script, *_args):
        # 取身份的调用带 url 参数；localStorage 的调用不带
        if _args:
            return self._profile
        return dict(self._ls)


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies
    async def cookies(self):
        return self._cookies


def _install(monkeypatch, sid, cookies, ls=None, url="https://www.douyin.com/jingxuan", age=0,
             state=None):
    monkeypatch.setitem(browser_qr._live, sid, {
        "page": FakePage(ls, url), "context": FakeContext(cookies),
        "created": time.time() - age, "browser": None, "pw": None, "xvfb": None,
        "state": state or {},
    })


@pytest.mark.asyncio
async def test_poll_pending_then_scanned_then_success(monkeypatch):
    sid = "s1"
    closed = []
    async def _close(s):
        closed.append(s)
    monkeypatch.setattr(browser_qr, "_close", _close)

    # 阶段1：还没扫码
    _install(monkeypatch, sid, [{"name": "UIFID_TEMP", "value": "x", "domain": ".douyin.com"}])
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "pending", r

    # 阶段2：扫了码、手机上还没确认 —— 以平台 check_qrconnect 的 status 为准
    _install(monkeypatch, sid, [{"name": "passport_csrf_token", "value": "csrf", "domain": ".douyin.com"}],
             state={"qr_status": "scanned"})
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "scanned", r.message
    assert "确认" in r.message

    # 关键回归：进登录页就有的 passport_csrf_token **不能**被当成"已扫码"（曾误报）
    _install(monkeypatch, sid, [{"name": "passport_csrf_token", "value": "csrf", "domain": ".douyin.com"}],
             state={"qr_status": "new"})
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "pending", "csrf token 不该触发已扫码提示"

    # 阶段3：登录成功，只有 localStorage.HasUserLogin（不给 LOGIN_STATUS cookie）
    _install(monkeypatch, sid,
             [{"name": "sid_tt", "value": "abc", "domain": ".douyin.com"}],
             ls={"HasUserLogin": "1", "user_sec_id": "SEC9", "user_id": "42"})
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "success", r
    assert r.credential["cookie"]
    assert r.credential["sec_user_id"] == "SEC9"
    assert r.credential["my_user_id"] == "42"
    assert closed == [sid]


@pytest.mark.asyncio
async def test_poll_expires(monkeypatch):
    sid = "s2"
    async def _close(s): pass
    monkeypatch.setattr(browser_qr, "_close", _close)
    _install(monkeypatch, sid, [], age=9999)
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "expired"


@pytest.mark.asyncio
async def test_poll_unknown_session(monkeypatch):
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": "nope"})
    assert r.status == "expired"

@pytest.mark.asyncio
async def test_poll_uses_self_profile_for_identity(monkeypatch):
    """登录后应从页面 self profile 取到 sec_uid/昵称（决定更新而非新建账号）。"""
    sid = "s3"
    async def _close(s): pass
    monkeypatch.setattr(browser_qr, "_close", _close)
    _install(monkeypatch, sid, [{"name": "sid_tt", "value": "abc", "domain": ".douyin.com"}],
             ls={"HasUserLogin": "1"})
    # 让假页面在"取身份"时返回 self profile
    browser_qr._live[sid]["page"] = FakePage(
        {"HasUserLogin": "1"}, "https://www.douyin.com/jingxuan",
        profile={"user": {"sec_uid": "SEC_REAL", "uid": "123", "nickname": "我的号"}},
    )
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "success"
    assert r.external_id == "SEC_REAL", "必须回 external_id，否则二次登录会新建账号"
    assert r.display_name == "我的号"
    assert r.credential["sec_user_id"] == "SEC_REAL"
    assert r.credential["my_user_id"] == "123"


@pytest.mark.asyncio
async def test_poll_falls_back_to_sid_tt_when_no_identity(monkeypatch):
    """平台不给身份时，至少用 sid_tt 当稳定标识，避免每次扫码都新建账号。"""
    sid = "s4"
    async def _close(s): pass
    monkeypatch.setattr(browser_qr, "_close", _close)
    _install(monkeypatch, sid, [{"name": "sid_tt", "value": "stable_sid", "domain": ".douyin.com"}],
             ls={"HasUserLogin": "1"})
    browser_qr._live[sid]["page"] = FakePage({"HasUserLogin": "1"})   # profile = None
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "success"
    assert r.external_id == "stable_sid"


class _Elt:
    def __init__(self, src):
        self._src = src
    async def get_property(self, name):
        return self._src


class _RefreshPage:
    """带 query_selector 的假页面：模拟抖音登录页自动换新二维码。"""
    def __init__(self, src, ls=None, url="https://www.douyin.com/jingxuan"):
        self._src = src
        self._ls = ls or {}
        self.url = url
    async def query_selector(self, sel):
        return _Elt(self._src)
    async def wait_for_selector(self, sel, timeout=None):
        # 假页面里二维码总在同一张：自愈用的"等待版"读取立即返回即可。
        return _Elt(self._src)
    async def evaluate(self, script, *_args):
        if _args:
            return None
        return dict(self._ls)


@pytest.mark.asyncio
async def test_poll_refreshes_qr_when_platform_reports_expired(monkeypatch):
    """平台二维码自然过期(约60s)时，若页面已换新码，应回传新图并保持 pending，而不是判死。"""
    sid = "s5"
    closed = []
    async def _close(s):
        closed.append(s)
    monkeypatch.setattr(browser_qr, "_close", _close)
    # 会话里记着旧码指纹；页面此刻已经换成新码
    monkeypatch.setitem(browser_qr._live, sid, {
        "page": _RefreshPage("data:image/png;base64,NEWCODE"),
        "context": FakeContext([]),
        "created": time.time(), "browser": None, "pw": None, "xvfb": None,
        "state": {"qr_status": "expired", "qr_hash": "OLDDIGEST"},
    })
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "pending", r
    assert r.qr_image_b64 == "NEWCODE", "应把页面上的新二维码回传给前端换图"
    assert closed == [], "自然过期不该销毁会话"


@pytest.mark.asyncio
async def test_poll_keeps_pending_between_expiry_and_refresh(monkeypatch):
    """平台报过期但页面还没换出新码(1~2s 窗口)：保持 pending 再等一轮，不判死。"""
    sid = "s6"
    closed = []
    async def _close(s):
        closed.append(s)
    monkeypatch.setattr(browser_qr, "_close", _close)
    import hashlib as _h
    same = "SAMECODE"
    monkeypatch.setitem(browser_qr._live, sid, {
        "page": _RefreshPage(f"data:image/png;base64,{same}"),  # 还是同一张旧码
        "context": FakeContext([]),
        "created": time.time(), "browser": None, "pw": None, "xvfb": None,
        # shown_hash 与页面当前码一致 = 用户屏幕上那张就是页面在轮询的这张，
        # 平台此刻报 expired、页面还没换新，属于 1~2s 换码窗口。
        "state": {
            "qr_status": "expired",
            "qr_hash": _h.md5(same.encode()).hexdigest(),
            "shown_hash": _h.md5(same.encode()).hexdigest(),
        },
    })
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "pending", r
    assert r.qr_image_b64 is None, "屏幕上已是最新的那张码，无需重复回传"
    assert closed == [], "换码窗口内不该销毁会话"


@pytest.mark.asyncio
async def test_poll_confirmed_without_cookies_yet_does_not_regress_to_pending(monkeypatch):
    """手机确认后（check_qrconnect=confirmed）状态还没落盘时，不能倒退成「等待扫码」。

    回归背景（用户实测：Mac 上抖音扫码确认后界面毫无反应）：
    `qr_status_map` 声明了 confirmed→success，但轮询只处理 scanned 分支，
    confirmed 落到兜底 `return pending("等待扫码…")` —— 用户已经确认，界面却提示
    让他「再用 App 扫一次」，看起来就是点了没反应。
    """
    sid = "s-confirmed"
    async def _close(s): pass
    monkeypatch.setattr(browser_qr, "_close", _close)

    # confirmed，但 cookie/localStorage 都还没写（确认与落盘之间的窗口）
    _install(monkeypatch, sid,
             [{"name": "passport_csrf_token", "value": "csrf", "domain": ".douyin.com"}],
             state={"qr_status": "confirmed"})
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "confirmed", r.status
    # 关键：不是「等待扫码」，而是告知已在完成登录
    assert "等待扫码" not in r.message, r.message
    assert "确认" in r.message, r.message


@pytest.mark.asyncio
async def test_poll_confirmed_then_cookies_appear_succeeds(monkeypatch):
    """确认后 cookie 落盘，下一轮必须立刻判成功（而不是继续 pending）。"""
    sid = "s-confirmed-2"
    closed = []
    async def _close(s): closed.append(s)
    monkeypatch.setattr(browser_qr, "_close", _close)

    _install(monkeypatch, sid,
             [{"name": "passport_csrf_token", "value": "csrf", "domain": ".douyin.com"}],
             state={"qr_status": "confirmed"})
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "confirmed"

    _install(monkeypatch, sid,
             [{"name": "sid_tt", "value": "abc", "domain": ".douyin.com"}],
             ls={"HasUserLogin": "1", "user_sec_id": "SEC9", "user_id": "42"},
             state={"qr_status": "confirmed"})
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "success", r
    assert r.credential["sec_user_id"] == "SEC9"
    assert closed == [sid]


@pytest.mark.asyncio
async def test_poll_confirmed_stuck_reports_error_not_pending_forever(monkeypatch):
    """确认了但迟迟没落盘：给若干轮宽限后要如实报错，不能永远 pending 骗用户。"""
    sid = "s-confirmed-3"
    async def _close(s): pass
    monkeypatch.setattr(browser_qr, "_close", _close)
    # 宽限期是真实计时，这里把它压到极短再让时间真的流逝，验证"超限即报错"。
    monkeypatch.setattr(browser_qr, "_CONFIRMED_GRACE_SECONDS", 0.05)
    state = {"qr_status": "confirmed"}
    _install(monkeypatch, sid, [], state=state)

    # 宽限期内：先给"正在完成登录"，不是报错
    browser_qr._live[sid]["state"] = state
    first = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert first.status == "confirmed" and "确认" in first.message, first.message

    await asyncio.sleep(0.3)  # 超过宽限
    browser_qr._live[sid]["state"] = state
    last = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert last.status == "error", f"应最终报错，实际 {last.status}: {last.message}"
    assert "确认" in last.message or "登录状态" in last.message, last.message


@pytest.mark.asyncio
async def test_poll_self_heals_when_shown_qr_is_stale(monkeypatch):
    """屏幕上的码与页面当前码脱节时，即使平台还回 new 也要把新图发下去（自愈）。

    回归背景（macOS 实测：扫了没反应、抖音一直回 new）：
    抖音会自然换码，旧逻辑只在**观察到远端 expired** 时才发新图。轮换一旦没被观察到，
    用户屏幕就长期挂着一张页面已不再轮询的死码，怎么扫都是 new。
    现在每轮都对齐 shown_hash：不一致就回传当前码，把用户从死码里救出来。
    """
    import hashlib as _h
    sid = "s-stale"
    closed = []
    async def _close(s): closed.append(s)
    monkeypatch.setattr(browser_qr, "_close", _close)

    old_code = "OLDCODE"
    monkeypatch.setitem(browser_qr._live, sid, {
        "page": _RefreshPage("data:image/png;base64,NEWCODE"),  # 页面已经是新码
        "context": FakeContext([]),
        "created": time.time(), "browser": None, "pw": None, "xvfb": None,
        # 屏幕上那张 = 旧码；远端状态还是 new（正因如此旧逻辑不会补救）
        "state": {"qr_status": "new", "shown_hash": _h.md5(old_code.encode()).hexdigest()},
    })
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "pending", r
    assert r.qr_image_b64 == "NEWCODE", "必须把页面当前码发回前端，替换掉屏幕上的死码"
    assert closed == [], "自愈不该销毁会话"


@pytest.mark.asyncio
async def test_poll_no_requeue_when_shown_matches_page(monkeypatch):
    """屏幕上的码 == 页面当前码时不重复回传（避免每轮都推一张相同的图）。"""
    import hashlib as _h
    sid = "s-same"
    async def _close(s): pass
    monkeypatch.setattr(browser_qr, "_close", _close)
    same = "SAMECODE"
    monkeypatch.setitem(browser_qr._live, sid, {
        "page": _RefreshPage(f"data:image/png;base64,{same}"),
        "context": FakeContext([]),
        "created": time.time(), "browser": None, "pw": None, "xvfb": None,
        "state": {"qr_status": "new", "shown_hash": _h.md5(same.encode()).hexdigest()},
    })
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "pending", r
    assert r.qr_image_b64 is None, "同一张码无需每轮重推"


@pytest.mark.asyncio
async def test_poll_reports_risk_block_instead_of_pending_forever(monkeypatch):
    """抖音要求"去 App 完成验证"（error_code=2046）时必须如实报错，不能继续 pending。

    真机证据（Mac）：扫码后状态到 scanned，随后抖音改回
      error_code=2046, description="为保障你的账号安全，请前往抖音APP完成验证后再进行登录"
    旧逻辑只认 status（此时为 None），把它当"还没扫"继续 pending，
    用户对着"已扫码待确认"干等到会话超时 —— 是个纯粹的误导。
    """
    sid = "s-risk"
    closed = []
    async def _close(s): closed.append(s)
    monkeypatch.setattr(browser_qr, "_close", _close)

    _install(monkeypatch, sid, [], state={
        "qr_status": "scanned",
        "qr_error_code": 2046,
        "qr_error_desc": "为保障你的账号安全，请前往抖音APP完成验证后再进行登录",
    })
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "error", r
    assert "验证" in r.message, r.message
    assert closed == [sid], "命中风控后应结束会话，不再空转"


@pytest.mark.asyncio
async def test_poll_ignores_zero_error_code(monkeypatch):
    """error_code=0 是正常响应，不能误判成风控。"""
    sid = "s-ok0"
    async def _close(s): pass
    monkeypatch.setattr(browser_qr, "_close", _close)
    _install(monkeypatch, sid, [], state={"qr_status": "scanned", "qr_error_code": 0})
    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "scanned", r


@pytest.mark.asyncio
async def test_api_poll_does_not_kill_session_on_expired(monkeypatch, client):
    """API 层收到 expired 不能销毁会话 —— 抖音每 60s 必过期一次换新码。

    回归背景（真机：刚扫码就弹「二维码已过期」，而手机端已确认）：
    适配层对"平台自然过期"已自愈（抓新图、返回 pending），但 API 层还留着
    旧逻辑 `if poll.status in ("expired","error"): qr_sessions.remove(...)`，
    于是每 60 秒就把整次扫码会话删掉，下轮轮询变成"会话不存在"。
    """
    from app.api import accounts as accounts_api
    from app.services import qr_sessions

    sid = qr_sessions.create("douyin", {"browser_session": "x"})

    class _Poll:
        status = "expired"
        message = "二维码正在刷新，请稍候…"
        qr_image_b64 = None

    class _Adapter:
        async def qr_poll(self, data):
            return _Poll()

    monkeypatch.setattr(accounts_api, "get_adapter", lambda p: _Adapter())

    r = client.get(f"/api/accounts/qr-login/{sid}")
    assert r.status_code == 200, r.text
    # 会话必须还在：否则"换码"会被误判成"整次失败"。
    assert qr_sessions.get(sid) is not None, "expired 不该销毁会话（那是换码，不是失败）"

    qr_sessions.remove(sid)


@pytest.mark.asyncio
async def test_api_poll_ends_session_on_error(monkeypatch, client):
    """error 是真正失败（如风控拦截）：应结束会话，避免继续空转。"""
    from app.api import accounts as accounts_api
    from app.services import qr_sessions

    sid = qr_sessions.create("douyin", {"browser_session": "x"})

    class _Poll:
        status = "error"
        message = "请在手机抖音 App 内完成验证后重试"
        qr_image_b64 = None

    class _Adapter:
        async def qr_poll(self, data):
            return _Poll()

    monkeypatch.setattr(accounts_api, "get_adapter", lambda p: _Adapter())

    r = client.get(f"/api/accounts/qr-login/{sid}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "error"
    assert qr_sessions.get(sid) is None, "真失败应结束会话"


# ---------- 真机回归：扫码后界面报"会话不存在或已过期" ----------
# 用户实测（Mac 生产实例）：手机已扫码并点了确认，界面却弹「会话不存在或已过期，请重新发起」。
# 根因不在抖音，而在**我们回放终态的方式**：会话一旦结束就被从内存里彻底删掉，
# 于是任何迟到的轮询（前端 3s 一次，且换码/失败时可能并发；用户手快再点一次也会重叠）
# 都只会拿到一句与事实无关的"会话不存在或已过期"，把真实原因盖掉。


@pytest.mark.asyncio
async def test_ended_session_replays_real_reason_not_generic_expired(monkeypatch, client):
    """会话结束后，轮询必须如实回放真实原因（风控/确认超时），不能一律说"会话不存在"。

    旧行为：`qr_sessions.remove()` 直接 pop，第二次轮询 => "会话不存在或已过期"。
    真机上这会把「抖音要求去 App 验证」这类真实原因盖掉，用户被误导成"二维码过期"。
    """
    from app.api import accounts as accounts_api
    from app.services import qr_sessions

    sid = qr_sessions.create("douyin", {"browser_session": "x"})

    class _Poll:
        status = "error"
        message = "为保障你的账号安全，请前往抖音APP完成验证后再进行登录"
        qr_image_b64 = None

    class _Adapter:
        async def qr_poll(self, data):
            return _Poll()

    monkeypatch.setattr(accounts_api, "get_adapter", lambda p: _Adapter())

    first = client.get(f"/api/accounts/qr-login/{sid}").json()
    assert first["status"] == "error"

    # 迟到的第二轮：必须回放同一个真因，而不是"会话不存在或已过期"。
    second = client.get(f"/api/accounts/qr-login/{sid}").json()
    assert second["status"] == "error", second
    assert "验证" in second["message"], second
    assert "会话不存在" not in second["message"], "真因被通用的'会话不存在'盖掉了"

    qr_sessions.remove(sid)


def test_ended_success_replays_success_not_expired(client):
    """成功同样是终态：浏览器已关闭后若还有一轮在途轮询，不能把界面从成功打回失败。"""
    from app.services import qr_sessions

    sid = qr_sessions.create("douyin", {"browser_session": "x"})
    qr_sessions.finish_success(sid, account_id=7, message="登录成功，已自动提取 Cookie")

    r = client.get(f"/api/accounts/qr-login/{sid}").json()
    assert r["status"] == "success", r
    assert r["account_id"] == 7, r

    qr_sessions.remove(sid)


def test_ended_sessions_are_gc_ed():
    """终态只留一小会儿：不能在内存里无限堆积。"""
    from app.services import qr_sessions

    sid = qr_sessions.create("douyin", {"browser_session": "x"})
    qr_sessions.finish(sid, "error", "风控")
    assert qr_sessions.terminal_status(sid) is not None

    # 手工把结束时间拨到 TTL 之前
    qr_sessions._sessions[sid].ended_at = time.time() - qr_sessions._ENDED_TTL - 1
    qr_sessions.create("douyin", {"browser_session": "y"})  # create 会触发 GC
    assert qr_sessions.terminal_status(sid) is None, "过期终态应被回收"


def test_finished_session_is_not_handed_to_adapter():
    """已结束的会话不能再交给适配器轮询：浏览器上下文早就关了，再查会拿到假状态。"""
    from app.services import qr_sessions

    sid = qr_sessions.create("douyin", {"browser_session": "x"})
    qr_sessions.finish(sid, "error", "风控")
    assert qr_sessions.get(sid) is None, "已结束会话不该再返回给适配器"
    assert qr_sessions.terminal_status(sid) == ("error", "风控", None)

    qr_sessions.remove(sid)


def test_qr_ttl_uses_platform_expire_time():
    """倒计时用"这张码的寿命"，而不是会话 TTL(300s)。

    真机症状：界面显示"还剩 4 分多钟"，可屏幕上那张码其实只剩几十秒就作废了。
    """
    conf = {"qr_ttl": 60}
    # 平台给了权威的 expire_time -> 用它
    assert browser_qr._qr_remaining_ttl(conf, {"qr_expire_at": time.time() + 42}) in (41, 42)
    # 没有 expire_time -> 退回配置的单张码寿命
    assert browser_qr._qr_remaining_ttl(conf, {}) == 60
    # 平台不区分单张码寿命 -> None，前端沿用会话 TTL
    assert browser_qr._qr_remaining_ttl({}, {}) is None


def test_douyin_declares_short_qr_ttl():
    """抖音单张码实测只活 ~59s，配置里必须声明，否则前端又会拿 300s 骗用户。"""
    assert browser_qr._PLATFORM_CONF["douyin"].get("qr_ttl") == 60


def test_claim_is_single_winner():
    """并发轮询只能有一个抢到处理权：否则一次扫码会建出两个账号（真机出现过）。"""
    from app.services import qr_sessions

    sid = qr_sessions.create("douyin", {"browser_session": "x"})
    assert qr_sessions.claim(sid, "success", "ok") is True
    assert qr_sessions.claim(sid, "success", "ok") is False, "第二次必须抢不到"

    # 抢到的那一轮落库后补上 account_id，回放要带上它
    qr_sessions.finish_success(sid, account_id=11, message="登录成功")
    assert qr_sessions.terminal_status(sid) == ("success", "登录成功", 11)

    # 已结束的会话不再交给适配器（浏览器上下文已关）
    assert qr_sessions.get(sid) is None

    qr_sessions.remove(sid)


# ---------- 真机关键缺失环节：扫码确认后的「验证码中间页」 ----------
# 用户实测发现（并对照工作区内多平台采集工具的官方注释确认）：
# 抖音扫码确认后**常会跳到「验证码中间页」**，那个滑块必须在**电脑端**拖过去，
# 手机上点"确认登录"不算数。旧流程从不看页面被切走，只盯 check_qrconnect，
# 于是永远停在 scanned 直到超时——这就是"手机确认了、界面毫无反应"的真因。


@pytest.mark.asyncio
async def test_captcha_wall_detected_by_page_title(monkeypatch):
    """页面标题变成「验证码中间页」时必须识别出来（最可靠的判据，实测就是这个标题）。"""

    class _TitlePage:
        url = "https://www.douyin.com/"
        frames: list = []

        async def title(self):
            return "验证码中间页"

    hit = await browser_qr._detect_captcha_wall(_TitlePage())
    assert hit is not None, "标题为「验证码中间页」必须命中"
    assert "验证码中间页" in hit


@pytest.mark.asyncio
async def test_captcha_wall_detected_by_slider_iframe(monkeypatch):
    """出现 verifycenter/captcha 的 iframe（滑块本体）也要命中。"""

    class _Fr:
        url = "https://rmc.bytedance.com/verifycenter/captcha/v2?subtype=slide"

        async def locator(self, sel):
            class _L:
                async def count(self_inner):
                    return 0
            return _L()

    class _Page:
        url = "https://www.douyin.com/"
        frames = [_Fr()]

        async def title(self):
            return "抖音"

    hit = await browser_qr._detect_captcha_wall(_Page())
    assert hit is not None and "iframe" in hit


@pytest.mark.asyncio
async def test_captcha_wall_detected_by_slider_dom(monkeypatch):
    """只有滑块 DOM（没有标题/iframe 线索）时也要命中。

    注意选择器必须用实测结构：旧实现用的 #captcha-verify-image 已经不存在了，
    现在是 class="captcha-verify-image"——照抄旧 id 会永远命中不了。
    """

    class _L:
        def __init__(self, n):
            self._n = n

        async def count(self):
            return self._n

    class _Page:
        url = "https://www.douyin.com/"
        frames: list = []

        async def title(self):
            return "抖音"

        # 注意：Playwright 的 page.locator() 是**同步**返回 Locator、count() 才是 await。
        # 假页面必须照这个形状写，否则 await 会作用在协程上、永远命中不了。
        def locator(self, sel):
            return _L(1 if sel == ".captcha-verify-image" else 0)

    hit = await browser_qr._detect_captcha_wall(_Page())
    assert hit is not None and "captcha-verify-image" in hit


@pytest.mark.asyncio
async def test_normal_login_page_is_not_a_captcha_wall(monkeypatch):
    """正常登录页（只有常态反欺诈 SDK）不能被误判成验证码墙。

    抖音登录页总会加载 rmc-nocaptcha，误判会让每次扫码都报"要过滑块"。
    """

    class _Fr:
        url = "https://rmc.bytedance.com/rmc-nocaptcha/sdk.html"

        async def locator(self, sel):
            class _L:
                async def count(self):
                    return 0
            return _L()

    class _Page:
        url = "https://www.douyin.com/"
        frames = [_Fr()]

        async def title(self):
            return "抖音-记录美好生活"

        async def locator(self, sel):
            class _L:
                async def count(self):
                    return 0
            return _L()

    assert await browser_qr._detect_captcha_wall(_Page()) is None, "正常登录页不该判成验证码墙"


def test_missing_captcha_selectors_are_not_stale_ids():
    """防回归：不能再用已失效的 #captcha-verify-image（现在是 class）。"""
    for sel in browser_qr._CAPTCHA_SLIDER_SELECTORS:
        assert "#captcha-verify-image" not in sel, "旧 id 选择器已失效，必须用实测的 class"


def test_captcha_extends_session_deadline():
    """命中滑块后会话要延长存活时间。

    真机场景：用户第 4 分钟才扫码、再拖滑块，很容易超出默认 300s；若还按原 TTL
    掐掉，会在用户正操作时把浏览器关掉并报"已超时"。
    """
    import time as _t

    from app.config import settings

    # 模拟"用户第 250 秒才扫到码、随后命中滑块"
    sess = {"created": _t.time() - 250, "state": {}}
    base = browser_qr._session_deadline(sess)
    assert abs(base - (sess["created"] + settings.qr_session_ttl)) < 1, "未命中滑块时按原 TTL"

    sess["state"]["captcha_seen_at"] = _t.time()
    extended = browser_qr._session_deadline(sess)
    assert extended > base, "命中滑块后截止时间必须往后推（否则会用掉用户操作的时间）"
    assert abs(extended - (sess["state"]["captcha_seen_at"] + browser_qr._CAPTCHA_EXTRA_SECONDS)) < 1


def test_captcha_extension_never_shortens_default_ttl():
    """额外余量不能把原本更宽的截止时间缩短（命中得早时应仍按原 TTL）。"""
    import time as _t

    from app.config import settings

    sess = {"created": _t.time(), "state": {"captcha_seen_at": _t.time()}}
    # 刚建会话就命中滑块：原 TTL(300s) 比 extra(180s) 宽，必须保留 300s
    assert abs(browser_qr._session_deadline(sess) - (sess["created"] + settings.qr_session_ttl)) < 1


@pytest.mark.asyncio
async def test_poll_reports_captcha_state_with_actionable_message(monkeypatch):
    """轮询命中验证码墙时要如实上报 captcha 状态，并给出可操作指引。"""
    sid = "cap1"

    class _CapPage:
        url = "https://www.douyin.com/"
        frames: list = []

        async def title(self):
            return "验证码中间页"

        async def evaluate(self, script, *a):
            return {}

    monkeypatch.setitem(browser_qr._live, sid, {
        "page": _CapPage(), "context": FakeContext([]),
        "created": time.time(), "browser": None, "pw": None, "xvfb": None,
        "state": {}, "engine": "chrome", "user_visible": True,
    })

    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "captcha", r
    assert "滑块" in r.message
    # 必须点明"手机确认不算数"，否则用户会一直盯着手机重试
    assert "手机" in r.message


@pytest.mark.asyncio
async def test_captcha_without_visible_window_points_to_manual_cookie(monkeypatch):
    """NAS 等无可见窗口场景：不能骗用户"去拖滑块"（他看不到那个窗口）。

    容器里 Xvfb 是虚拟显示，程序能渲染但用户看不见、更没法操作——必须如实
    引导改用手动贴 Cookie。
    """
    sid = "cap2"

    class _CapPage:
        url = "https://www.douyin.com/"
        frames: list = []

        async def title(self):
            return "验证码中间页"

        async def evaluate(self, script, *a):
            return {}

    monkeypatch.setitem(browser_qr._live, sid, {
        "page": _CapPage(), "context": FakeContext([]),
        "created": time.time(), "browser": None, "pw": None, "xvfb": None,
        "state": {}, "engine": "chromium", "user_visible": False,
    })

    r = await browser_qr.browser_qr_poll("douyin", {"browser_session": sid})
    assert r.status == "captcha", r
    assert "Cookie" in r.message, "无窗口时必须引导改用手动 Cookie"
