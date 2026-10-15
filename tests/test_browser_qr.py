"""扫码登录（browser_qr）配置与判定测试：不启动真实浏览器。"""

import pytest

from app.modules import browser_qr


def test_platform_config_covers_kuaishou_zhihu():
    """快手/知乎必须在 _PLATFORM_CONF 中（抖音/小红书既有）。"""
    assert set(browser_qr._PLATFORM_CONF) >= {"douyin", "xhs", "kuaishou", "zhihu"}
    ks = browser_qr._PLATFORM_CONF["kuaishou"]
    assert ks["needs_display"] is True
    assert ks["login_button"] and ks["qr_selector"]
    assert "passport.kuaishou.com" in ks["url"]
    zh = browser_qr._PLATFORM_CONF["zhihu"]
    assert zh["needs_display"] is False
    assert zh["qr_kind"] == "canvas"


def test_stealth_is_a_mit_dependency_not_vendored_script():
    """反检测改用 MIT 的 playwright-stealth 依赖，不再随包携带复制脚本。"""
    from pathlib import Path

    vendored = Path(browser_qr.__file__).parent / "crawler" / "libs" / "stealth.min.js"
    assert not vendored.exists()
    pyproject = Path(browser_qr.__file__).resolve().parents[2] / "pyproject.toml"
    assert "playwright-stealth" in pyproject.read_text(encoding="utf-8")


def test_login_flag_detection():
    """成功判定：抖音多重信号、小红书 web_session、快手 passToken、知乎 z_c0。"""
    assert browser_qr._is_logged_in("douyin", {"LOGIN_STATUS": "1"}) is True
    assert browser_qr._is_logged_in("douyin", {"LOGIN_STATUS": "0"}) is False
    assert browser_qr._is_logged_in("xhs", {"web_session": "abc"}) is True
    assert browser_qr._is_logged_in("kuaishou", {"passToken": "abc"}) is True
    assert browser_qr._is_logged_in("kuaishou", {"did": "x"}) is False
    assert browser_qr._is_logged_in("zhihu", {"z_c0": "abc"}) is True
    assert browser_qr._is_logged_in("zhihu", {}) is False


def test_douyin_local_storage_signal():
    """回归：抖音 PC 端登录主要写 localStorage.HasUserLogin，只看 cookie 会永远 pending。

    实测用户扫码授权后无任何反应、一直停在"等待扫码"直到超时。
    登录态判定必须同时看 localStorage 与 cookie（平台 PC 端主要写前者）。
    """
    assert browser_qr._is_logged_in("douyin", {}, {"HasUserLogin": "1"}) is True
    assert browser_qr._is_logged_in("douyin", {}, {"HasUserLogin": "0"}) is False
    assert browser_qr._is_logged_in("douyin", {}, {}) is False
    # cookie 仍是有效信号（部分路径只写 cookie）
    assert browser_qr._is_logged_in("douyin", {"sessionid": "x"}, {}) is True
    assert browser_qr._is_logged_in("douyin", {"sid_tt": "x"}, {}) is True


def test_douyin_logged_out_wins():
    """LOGIN_STATUS=0 是明确登出，不能被 sessionid 之类的兜底信号翻成已登录。"""
    assert browser_qr._is_logged_in(
        "douyin", {"LOGIN_STATUS": "0", "sessionid": "stale"}, {"HasUserLogin": "1"}
    ) is False


def test_douyin_login_by_url():
    """登录后直接跳个人页（cookie 写入有延迟）也应判定成功。"""
    assert browser_qr._is_logged_in("douyin", {}, {}, "https://www.douyin.com/user/self") is True
    assert browser_qr._is_logged_in("douyin", {}, {}, "https://www.douyin.com/jingxuan") is False


def test_douyin_identity_from_local_storage():
    """抖音身份回填：能从 localStorage/cookie 捞出 sec_user_id / my_user_id。"""
    ident = browser_qr._douyin_identity(
        {"user_sec_id": "SEC123", "user_id": "456"}, {}
    )
    assert ident == {"sec_user_id": "SEC123", "my_user_id": "456"}
    # 取不到就留空，不报错
    assert browser_qr._douyin_identity({}, {}) == {"sec_user_id": "", "my_user_id": ""}
    # cookie 兜底
    assert browser_qr._douyin_identity({}, {"uid_tt": "789"})["my_user_id"] == "789"


def test_credential_extra_fields():
    """抖音额外回填 sec_user_id/my_user_id；快手/知乎只回 cookie 字符串。"""
    assert set(browser_qr._extra_credential("douyin")) == {"sec_user_id", "my_user_id"}
    assert browser_qr._extra_credential("kuaishou") == {}
    assert browser_qr._extra_credential("zhihu") == {}


def test_unsupported_platform_raises():
    with pytest.raises(KeyError):
        browser_qr._PLATFORM_CONF["unknown_platform"]


@pytest.mark.asyncio
async def test_poll_unknown_session_expires():
    """不存在的会话立刻返回 expired（不触碰浏览器）。"""
    poll = await browser_qr.browser_qr_poll("zhihu", {"browser_session": "nope"})
    assert poll.status == "expired"


# ---------- 引擎选择：Mac 用真实浏览器，NAS 必须照旧 ----------
# 背景：抖音对无头/自动化特征敏感。无头 Chromium 得靠 stealth 把 webdriver 抹成 false、
# 伪造 plugins、并把 UA 谎称成 "Windows Chrome 126"（真实环境其实是 macOS 无头）——
# 这组自相矛盾本身就是风控特征。真实 Chrome 有头模式天然自洽（实测 Mac 上
# webdriver=false / plugins=5 / UA=真实 Chrome 155）。
# 硬约束：NAS 是无桌面的 Docker 容器（有 Xvfb、没有 Chrome），必须继续走无头 Chromium。


def test_nas_container_falls_back_to_chromium(monkeypatch):
    """NAS 场景：容器里没有真实 Chrome、也没有桌面 -> 必须落回 chromium。

    这条是本次改动最关键的回归：Mac 上的改进绝不能改变 NAS 的行为。
    """
    monkeypatch.setattr(browser_qr, "_find_real_browser", lambda: None)
    monkeypatch.setattr(browser_qr, "_can_open_window", lambda: False)
    monkeypatch.setattr(browser_qr.settings, "qr_browser_engine", "auto", raising=False)
    assert browser_qr._resolve_engine({}) == "chromium"


def test_mac_desktop_uses_real_chrome(monkeypatch):
    """Mac 场景：有真实 Chrome + 有桌面 -> 用 chrome（有头真实浏览器）。"""
    monkeypatch.setattr(browser_qr, "_find_real_browser", lambda: "/Applications/Google Chrome.app/...")
    monkeypatch.setattr(browser_qr, "_can_open_window", lambda: True)
    monkeypatch.setattr(browser_qr.settings, "qr_browser_engine", "auto", raising=False)
    assert browser_qr._resolve_engine({}) == "chrome"


def test_explicit_chromium_always_wins(monkeypatch):
    """显式关掉真实浏览器时必须听配置（排障/回滚用）。"""
    monkeypatch.setattr(browser_qr, "_find_real_browser", lambda: "/Applications/Google Chrome.app/...")
    monkeypatch.setattr(browser_qr, "_can_open_window", lambda: True)
    monkeypatch.setattr(browser_qr.settings, "qr_browser_engine", "chromium", raising=False)
    assert browser_qr._resolve_engine({}) == "chromium"


def test_chrome_requested_without_desktop_degrades(monkeypatch):
    """配了 chrome 但没桌面（如容器）：降级而不是硬报错，扫码仍可用。"""
    monkeypatch.setattr(browser_qr, "_find_real_browser", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(browser_qr, "_can_open_window", lambda: False)
    monkeypatch.setattr(browser_qr.settings, "qr_browser_engine", "chrome", raising=False)
    assert browser_qr._resolve_engine({}) == "chromium"


def test_chrome_requested_without_binary_degrades(monkeypatch):
    """配了 chrome 但机器上没装：降级，别让用户彻底没法登录。"""
    monkeypatch.setattr(browser_qr, "_find_real_browser", lambda: None)
    monkeypatch.setattr(browser_qr, "_can_open_window", lambda: True)
    monkeypatch.setattr(browser_qr.settings, "qr_browser_engine", "chrome", raising=False)
    assert browser_qr._resolve_engine({}) == "chromium"


def test_real_browser_probe_finds_nothing_in_container():
    """容器里不该误判出真实 Chrome（用不存在的路径清单验证探测逻辑本身）。"""
    import os
    import sys as _sys

    real_exists = os.path.isfile
    try:
        os.path.isfile = lambda p: False  # 模拟干净容器
        assert browser_qr._find_real_browser() is None
    finally:
        os.path.isfile = real_exists
    # 探测清单必须覆盖三个平台，否则某平台会静默永远走 chromium
    for plat in ("darwin", "win32", "linux"):
        assert browser_qr._REAL_BROWSER_PATHS.get(plat), f"{plat} 缺少真实浏览器探测路径"


def test_temp_profile_is_not_created_when_shared_works(monkeypatch):
    """共享 profile 成功时不该留下一次性临时目录。

    真机踩过：早期实现无条件 mkdtemp，每扫一次就在临时区留一个空目录，
    180s 内积了 4 个。这里用一个假 pw 验证"按需创建"。
    """
    import asyncio
    import tempfile as _tempfile

    from app.config import settings

    created = {"mkdtemp": 0}
    real_mkdtemp = _tempfile.mkdtemp

    def counting_mkdtemp(*a, **kw):
        created["mkdtemp"] += 1
        return real_mkdtemp(*a, **kw)

    monkeypatch.setattr(_tempfile, "mkdtemp", counting_mkdtemp)

    class _FakeCtx:
        async def close(self):
            return None

    class _FakePW:
        class chromium:  # noqa: N801
            @staticmethod
            async def launch_persistent_context(user_data_dir, **kw):
                # 共享目录"启动成功"
                return _FakeCtx()

        async def stop(self):
            return None

    class _FakeAPW:
        async def start(self):
            return _FakePW()

    monkeypatch.setattr(browser_qr, "_require_playwright", lambda: lambda: _FakeAPW())
    monkeypatch.setattr(browser_qr, "_find_real_browser", lambda: "/Applications/Google Chrome.app/x")
    monkeypatch.setattr(browser_qr, "_can_open_window", lambda: True)
    monkeypatch.setattr(settings, "qr_browser_engine", "auto", raising=False)
    monkeypatch.setattr(settings, "qr_chrome_profile_dir", None, raising=False)

    # 让页面相关调用短路：我们只关心 profile 的创建时机
    async def _fake_img(page, selector):
        return "ZmFrZQ=="

    monkeypatch.setattr(browser_qr, "_qr_image_b64", _fake_img)

    class _FakePage:
        url = "https://www.douyin.com/jingxuan"

        async def goto(self, *a, **kw):
            return None

        def on(self, *a, **kw):
            return None

    class _CtxWithPage(_FakeCtx):
        async def new_page(self):
            return _FakePage()

    class _FakePW2(_FakePW):
        class chromium:  # noqa: N801
            @staticmethod
            async def launch_persistent_context(user_data_dir, **kw):
                return _CtxWithPage()

    class _FakeAPW2:
        async def start(self):
            return _FakePW2()

    monkeypatch.setattr(browser_qr, "_require_playwright", lambda: lambda: _FakeAPW2())

    async def _run():
        s = await browser_qr.browser_qr_start("douyin")
        sid = s.session_data["browser_session"]
        await browser_qr._close(sid)

    asyncio.run(_run())
    assert created["mkdtemp"] == 0, "共享 profile 成功时不该创建一次性临时目录"
