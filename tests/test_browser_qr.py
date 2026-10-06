"""扫码登录（browser_qr）配置与判定测试：不启动真实浏览器。"""

import pytest

from app.modules import browser_qr


def test_platform_config_covers_kuaishou_zhihu():
    """快手/知乎必须在 _PLATFORM_CONF 中（抖音/小红书既有）。"""
    assert set(browser_qr._PLATFORM_CONF) >= {"douyin", "xhs", "kuaishou", "zhihu"}
    ks = browser_qr._PLATFORM_CONF["kuaishou"]
    assert ks["needs_display"] is True
    assert ks["login_button"] and ks["qr_selector"]
    zh = browser_qr._PLATFORM_CONF["zhihu"]
    assert zh["needs_display"] is False
    assert zh["qr_kind"] == "canvas"


def test_stealth_script_available():
    assert browser_qr._STEALTH_JS.exists()


def test_login_flag_detection():
    """成功判定：抖音 LOGIN_STATUS、小红书 web_session、快手 passToken、知乎 z_c0。"""
    assert browser_qr._is_logged_in("douyin", {"LOGIN_STATUS": "1"}) is True
    assert browser_qr._is_logged_in("douyin", {"LOGIN_STATUS": "0"}) is False
    assert browser_qr._is_logged_in("xhs", {"web_session": "abc"}) is True
    assert browser_qr._is_logged_in("kuaishou", {"passToken": "abc"}) is True
    assert browser_qr._is_logged_in("kuaishou", {"did": "x"}) is False
    assert browser_qr._is_logged_in("zhihu", {"z_c0": "abc"}) is True
    assert browser_qr._is_logged_in("zhihu", {}) is False


def test_credential_extra_fields():
    """抖音额外回填 sec_user_id/my_user_id；快手/知乎只回 cookie 字符串。"""
    assert set(browser_qr._extra_credential("douyin")) == {"sec_user_id", "my_user_id"}
    assert browser_qr._extra_credential("kuaishou") == {}
    assert browser_qr._extra_credential("zhihu") == {}


def test_unsupported_platform_raises():
    with pytest.raises(KeyError):
        browser_qr._PLATFORM_CONF["tieba"]


@pytest.mark.asyncio
async def test_poll_unknown_session_expires():
    """不存在的会话立刻返回 expired（不触碰浏览器）。"""
    poll = await browser_qr.browser_qr_poll("zhihu", {"browser_session": "nope"})
    assert poll.status == "expired"
