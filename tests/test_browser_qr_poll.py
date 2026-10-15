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
