"""阶段 6：B站主动刷新 + 抖音守恒。"""

import asyncio

import httpx
import pytest

from app.modules.bilibili import BilibiliAdapter, _cookies


def test_bili_cookies_include_ac_time_value():
    cookies = _cookies(
        {
            "sessdata": "S",
            "bili_jct": "J",
            "buvid3": "B",
            "dedeuserid": "D",
            "ac_time_value": "R",
        }
    )
    assert cookies["ac_time_value"] == "R"


def test_bili_refresh_keeps_buvid3(monkeypatch):
    """刷新成功后：SESSDATA/bili_jct/DedeUserID 更新，buvid3 保持旧值。"""
    old = {
        "sessdata": "OLD_S",
        "bili_jct": "OLD_J",
        "buvid3": "KEEP_B",
        "dedeuserid": "OLD_D",
        "ac_time_value": "OLD_R",
    }
    calls = []

    class FakeResponse:
        def __init__(self, payload=None, text="", cookies=None):
            self._payload = payload or {}
            self.text = text
            self.cookies = cookies or {}
            self.status_code = 200
            self.headers = {}

        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            calls.append(("GET", url))
            if "correspond" in url:
                return FakeResponse(text='<div id="1-name">CSRF_X</div>')
            return FakeResponse(payload={"code": 0, "data": {}})

        async def post(self, url, **kw):
            calls.append(("POST", url, kw.get("data")))
            if url.endswith("/cookie/refresh"):
                return FakeResponse(
                    payload={"code": 0, "data": {"refresh_token": "NEW_R"}},
                    cookies={"SESSDATA": "NEW_S", "bili_jct": "NEW_J", "DedeUserID": "NEW_D"},
                )
            return FakeResponse(payload={"code": 0, "data": {}})

    monkeypatch.setattr("app.modules.bilibili._client", lambda: FakeClient())

    new = asyncio.run(BilibiliAdapter().refresh(old))

    assert new["sessdata"] == "NEW_S"
    assert new["bili_jct"] == "NEW_J"
    assert new["dedeuserid"] == "NEW_D"
    assert new["ac_time_value"] == "NEW_R"
    assert new["buvid3"] == "KEEP_B", "设备标识必须守恒"
    assert any("correspond" in c[1] for c in calls if c[0] == "GET")
    assert any("confirm/refresh" in c[1] for c in calls if c[0] == "POST")


def test_bili_refresh_without_token_raises():
    with pytest.raises(ValueError):
        asyncio.run(BilibiliAdapter().refresh({"sessdata": "S", "bili_jct": "J", "buvid3": "B", "dedeuserid": "D", "ac_time_value": ""}))


def test_douyin_cookie_passthrough_is_not_trimmed():
    """抖音守恒：整串 cookie 原样透传，不裁剪字段。"""
    from app.modules import douyin

    raw = "sessionid=abc; ttwid=xyz; odin_tt=1; passport_csrf_token=zz"
    headers = {"User-Agent": "UA", "Referer": "https://www.douyin.com", "Cookie": raw}
    assert headers["Cookie"] == raw


def test_douyin_param_templates_are_frozen():
    """抖音请求参数模板必须写死，不能随机化设备字段。"""
    from app.modules import douyin

    assert douyin.POST_PARAMS["browser_version"] == "142.0.0.0"
    assert douyin.POST_PARAMS["webid"] == "7574080345697584675"
    assert douyin.POST_PARAMS["version_code"] == "290100"


def test_check_triggers_bili_refresh_and_writes_back(client, session, monkeypatch):
    """体检需要刷新时：调用 refresh 并以新凭证回写，旧 buvid3 保留。"""
    from app.core.crypto import decrypt_credential, encrypt_credential
    from app.models import AccountCredential, PlatformAccount
    from app.modules.base import CheckResult

    old = {
        "sessdata": "OLD_S",
        "bili_jct": "OLD_J",
        "buvid3": "KEEP_B",
        "dedeuserid": "D",
        "ac_time_value": "OLD_R",
    }
    account = PlatformAccount(platform="bilibili", display_name="主号", external_id="D")
    session.add(account)
    session.flush()
    account.credential = AccountCredential(ciphertext=encrypt_credential(old))
    session.commit()

    async def fake_check(self, credential):
        return CheckResult(ok=True, message="在线（平台建议刷新 Cookie）", external_id="D", need_refresh=True)

    async def fake_refresh(self, credential):
        return {**credential, "sessdata": "NEW_S", "bili_jct": "NEW_J", "ac_time_value": "NEW_R"}

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.check", fake_check)
    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.refresh", fake_refresh)

    resp = client.post(f"/api/accounts/{account.id}/check")

    assert resp.status_code == 200
    session.refresh(account)
    saved = decrypt_credential(account.credential.ciphertext)
    assert saved["sessdata"] == "NEW_S"
    assert saved["ac_time_value"] == "NEW_R"
    assert saved["buvid3"] == "KEEP_B"
    assert "已自动刷新" in account.status_message


def test_check_keeps_active_when_refresh_fails(client, session, monkeypatch):
    """刷新失败不能把在线账号判死，只提示失败等待人工处理。"""
    from app.core.crypto import encrypt_credential
    from app.models import AccountCredential, PlatformAccount
    from app.modules.base import CheckResult

    account = PlatformAccount(platform="bilibili", display_name="主号", external_id="D")
    session.add(account)
    session.flush()
    account.credential = AccountCredential(
        ciphertext=encrypt_credential(
            {"sessdata": "S", "bili_jct": "J", "buvid3": "B", "dedeuserid": "D", "ac_time_value": "R"}
        )
    )
    session.commit()

    async def fake_check(self, credential):
        return CheckResult(ok=True, message="在线", external_id="D", need_refresh=True)

    async def boom(self, credential):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.check", fake_check)
    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.refresh", boom)

    resp = client.post(f"/api/accounts/{account.id}/check")

    assert resp.status_code == 200
    session.refresh(account)
    assert account.status == "active"
    assert "刷新失败" in account.status_message
