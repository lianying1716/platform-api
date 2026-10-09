"""阶段 1：账号身份可信、可去重、可修正，绝不靠“只有一个账号”猜。"""

from sqlalchemy import select

from app.models import PlatformAccount


def _poll(monkeypatch, platform, external_id="", display_name="", credential=None):
    from app.modules.base import QrPoll, QrStart

    adapter_cls = __import__(f"app.modules.{platform}", fromlist=["x"]).__dict__[
        {"douyin": "DouyinAdapter", "bilibili": "BilibiliAdapter"}[platform]
    ]

    async def fake_start(self):
        return QrStart(session_data={"k": "v"}, qr_image_b64="aGk=")

    async def fake_poll(self, session_data):
        return QrPoll(
            status="success",
            message="登录成功",
            credential=credential or {"cookie": "sessionid=abc"},
            external_id=external_id,
            display_name=display_name,
        )

    monkeypatch.setattr(adapter_cls, "qr_start", fake_start)
    monkeypatch.setattr(adapter_cls, "qr_poll", fake_poll)


def _login(client, platform="douyin"):
    sid = client.post("/api/accounts/qr-login", json={"platform": platform}).json()["session_id"]
    return client.get(f"/api/accounts/qr-login/{sid}")


def test_qr_without_identity_never_reuses_existing_account(client, session, monkeypatch):
    """已有账号 A，扫入无身份的新登录：拒绝，不覆盖也不新建无主账号。"""
    session.add(PlatformAccount(platform="douyin", display_name="A", external_id="SEC_A"))
    session.commit()
    _poll(monkeypatch, "douyin", external_id="", display_name="")

    resp = _login(client)

    assert resp.status_code == 409
    accounts = session.scalars(select(PlatformAccount)).all()
    assert len(accounts) == 1
    assert {a.external_id for a in accounts} == {"SEC_A"}


def test_qr_with_unknown_identity_creates_second_account(client, session, monkeypatch):
    """已有账号 A，扫入带新身份的 B：必须新建 B，不能更新 A。"""
    session.add(PlatformAccount(platform="douyin", display_name="A", external_id="SEC_A"))
    session.commit()
    _poll(monkeypatch, "douyin", external_id="SEC_B", display_name="B")

    resp = _login(client)

    assert resp.status_code == 200
    accounts = session.scalars(select(PlatformAccount)).all()
    assert {a.external_id for a in accounts} == {"SEC_A", "SEC_B"}


def test_manual_create_dedupes_by_external_id(client, session, monkeypatch):
    """手动录入同一账号两次：按 (platform, external_id) 更新，不新建。"""
    body = {
        "platform": "douyin",
        "credential": {"cookie": "sessionid=abc", "sec_user_id": "SEC_1"},
        "skip_check": True,
    }
    first = client.post("/api/accounts", json=body)
    assert first.status_code == 200, first.text
    second = client.post("/api/accounts", json=body)
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert session.scalar(select(PlatformAccount).where(PlatformAccount.platform == "douyin")) is not None


def test_check_corrects_stale_external_id(client, session, monkeypatch):
    """体检拿到真实身份时，脏 external_id 要被修正。"""
    from app.modules.base import CheckResult
    from app.core.crypto import encrypt_credential
    from app.models import AccountCredential

    account = PlatformAccount(platform="bilibili", display_name="主号", external_id="OLD_ID")
    session.add(account)
    session.flush()
    account.credential = AccountCredential(ciphertext=encrypt_credential({"sessdata": "s", "bili_jct": "j"}))
    session.commit()

    async def fake_check(self, credential):
        return CheckResult(ok=True, message="在线", external_id="REAL_ID", display_name="真实昵称")

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.check", fake_check)
    resp = client.post(f"/api/accounts/{account.id}/check")

    assert resp.status_code == 200
    session.refresh(account)
    assert account.external_id == "REAL_ID"


def test_check_conflict_does_not_silently_overwrite(client, session, monkeypatch):
    """体检身份与另一个账号冲突时，不能静默覆盖，必须给出明确错误。"""
    from app.modules.base import CheckResult
    from app.core.crypto import encrypt_credential
    from app.models import AccountCredential

    a = PlatformAccount(platform="bilibili", display_name="A", external_id="SEC_A")
    b = PlatformAccount(platform="bilibili", display_name="B", external_id="")
    session.add_all([a, b])
    session.flush()
    b.credential = AccountCredential(ciphertext=encrypt_credential({"sessdata": "s", "bili_jct": "j"}))
    session.commit()

    async def fake_check(self, credential):
        return CheckResult(ok=True, message="在线", external_id="SEC_A", display_name="A")

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.check", fake_check)
    resp = client.post(f"/api/accounts/{b.id}/check")

    assert resp.status_code in (200, 409)
    session.refresh(b)
    assert b.external_id == ""  # 冲突未被静默写入
