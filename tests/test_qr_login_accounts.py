"""扫码登录落库：二次登录必须更新原账号，不能每次新建。

回归背景：抖音的 qr_poll 原先既不回 external_id 也不回 display_name，
于是每扫一次码就新建一个账号（实测两次登录 → 2 个账号，external_id 都是空）。
"""

import pytest

from app.models import PlatformAccount


def _fake_qr(monkeypatch, platform, poll_result):
    from app.modules import browser_qr  # noqa: F401
    from app.modules.base import QrPoll, QrStart

    adapter_cls = __import__(f"app.modules.{platform}", fromlist=["x"]).__dict__[
        {"douyin": "DouyinAdapter", "bilibili": "BilibiliAdapter"}[platform]
    ]

    async def fake_start(self):
        return QrStart(session_data={"browser_session": "x"}, qr_image_b64="aGk=")

    async def fake_poll(self, session_data):
        return poll_result

    monkeypatch.setattr(adapter_cls, "qr_start", fake_start)
    monkeypatch.setattr(adapter_cls, "qr_poll", fake_poll)


def _login(client, platform="douyin", display_name=""):
    r = client.post("/api/accounts/qr-login", json={"platform": platform, "display_name": display_name})
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


def test_missing_external_id_reuses_single_account_of_platform(client, session, monkeypatch):
    """平台没回身份标识时，也不能每扫一次就多一个号（单账号平台复用）。"""
    from app.modules.base import QrPoll

    poll = QrPoll(status="success", message="登录成功",
                  credential={"cookie": "sessionid=abc"}, external_id="", display_name="")
    _fake_qr(monkeypatch, "douyin", poll)

    a1 = _login(client).json()
    a2 = _login(client).json()
    assert a1["account_id"] == a2["account_id"]
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


def test_poll_fills_external_id_later(client, session, monkeypatch):
    """先无身份建号，后续拿到身份时补上，而不是再建一个。"""
    from app.modules.base import QrPoll

    poll = QrPoll(status="success", message="ok", credential={"cookie": "c=1"},
                  external_id="", display_name="")
    _fake_qr(monkeypatch, "douyin", poll)
    first = _login(client).json()["account_id"]

    poll2 = QrPoll(status="success", message="ok", credential={"cookie": "c=2"},
                   external_id="SEC_X", display_name="补上的名字")
    _fake_qr(monkeypatch, "douyin", poll2)
    second = _login(client).json()["account_id"]

    assert first == second
    acc = session.query(PlatformAccount).one()
    assert acc.external_id == "SEC_X"
    assert acc.display_name == "补上的名字"
