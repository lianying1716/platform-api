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


def test_stealth_script_available():
    assert browser_qr._STEALTH_JS.exists()


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
    参考实现（MediaCrawler douyin/client.py:pong）同时看 localStorage 与 cookie。
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
        browser_qr._PLATFORM_CONF["tieba"]


@pytest.mark.asyncio
async def test_poll_unknown_session_expires():
    """不存在的会话立刻返回 expired（不触碰浏览器）。"""
    poll = await browser_qr.browser_qr_poll("zhihu", {"browser_session": "nope"})
    assert poll.status == "expired"
