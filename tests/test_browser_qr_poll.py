"""浏览器扫码轮询的状态流转（用假 page/context，不启动真实浏览器）。

回归背景：抖音扫码登录"扫了没反应"——
- 成功判定只看 LOGIN_STATUS cookie，而抖音 PC 端主要写 localStorage.HasUserLogin，
  导致扫完码仍判为未登录、永远停在 pending；
- 轮询从不返回 scanned，用户扫码后没有任何反馈；
- 前端轮询 catch{} 静默停掉，超时也不提示。
"""
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
