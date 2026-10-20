"""Cookie updates must target an explicit account when a platform has many."""

from app.models import AccountCredential, PlatformAccount
from app.core.crypto import decrypt_credential, encrypt_credential


def _account(session, name, external_id, *, platform="bilibili"):
    account = PlatformAccount(
        platform=platform,
        display_name=name,
        external_id=external_id,
        credential_kind="cookie",
        status="active",
    )
    session.add(account)
    session.flush()
    session.add(AccountCredential(account_id=account.id, ciphertext=encrypt_credential({"cookie": "old"})))
    session.commit()
    session.refresh(account)
    return account


def _key(client):
    response = client.post("/api/ai/keys", json={"name": "cookie-target", "scopes": ["operate"]})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['api_key']}"}


COOKIE = "SESSDATA=new; bili_jct=x"


def _cookie(value=COOKIE):
    return value


def test_cookie_update_requires_explicit_target_with_multiple_accounts(client, session):
    first = _account(session, "主号", "UID1")
    second = _account(session, "小号", "UID2")
    headers = _key(client)

    response = client.post(
        "/api/skills/accounts/cookie",
        headers=headers,
        json={"platform": "bilibili", "cookie": _cookie()},
    )
    assert response.status_code == 409, response.text
    session.expire_all()
    assert decrypt_credential(session.get(PlatformAccount, first.id).credential.ciphertext)["cookie"] == "old"
    assert decrypt_credential(session.get(PlatformAccount, second.id).credential.ciphertext)["cookie"] == "old"


def test_cookie_update_accepts_explicit_account_id(client, session):
    first = _account(session, "主号", "UID1")
    second = _account(session, "小号", "UID2")
    headers = _key(client)
    response = client.post(
        "/api/skills/accounts/cookie",
        headers=headers,
        json={
            "platform": "bilibili",
            "account_id": second.id,
            "cookie": _cookie(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["account_id"] == second.id
    session.expire_all()
    assert decrypt_credential(session.get(PlatformAccount, first.id).credential.ciphertext)["cookie"] == "old"
    updated = decrypt_credential(session.get(PlatformAccount, second.id).credential.ciphertext)
    assert updated["sessdata"] == "new" and updated["bili_jct"] == "x"


def test_cookie_update_accepts_external_id_and_rejects_cross_platform(client, session):
    first = _account(session, "主号", "UID1")
    headers = _key(client)
    response = client.post(
        "/api/skills/accounts/cookie",
        headers=headers,
        json={"platform": "bilibili", "external_id": "UID1", "cookie": _cookie("SESSDATA=one; bili_jct=x")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["account_id"] == first.id
    mismatch = client.post(
        "/api/skills/accounts/cookie",
        headers=headers,
        json={"platform": "douyin", "account_id": first.id, "cookie": "sessionid=x"},
    )
    assert mismatch.status_code == 409, mismatch.text


def test_cookie_update_allows_single_account_without_target(client, session):
    account = _account(session, "唯一号", "UID1")
    headers = _key(client)
    response = client.post(
        "/api/skills/accounts/cookie",
        headers=headers,
        json={"platform": "bilibili", "cookie": _cookie()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["account_id"] == account.id


def test_cookie_update_creates_new_account_when_platform_is_empty(client, session):
    headers = _key(client)
    response = client.post(
        "/api/skills/accounts/cookie",
        headers=headers,
        json={"platform": "bilibili", "cookie": _cookie(), "display_name": "新号"},
    )
    assert response.status_code == 200, response.text
    assert session.query(PlatformAccount).filter_by(platform="bilibili").count() == 1


def test_skill_qr_start_forwards_account_id(client, monkeypatch):
    from app.services import qr_sessions
    from app.modules.base import QrStart

    seen = {}

    async def fake_start(_self):
        return QrStart(session_data={"x": 1}, qr_image_b64="aGk=")

    class FakeAdapter:
        supports_qr = True
        label = "fake"

        async def qr_start(self):
            return await fake_start(self)

    import app.modules

    monkeypatch.setattr(app.modules, "get_adapter", lambda _p: FakeAdapter())
    original_create = qr_sessions.create

    def capture(platform, data, display_name="", account_id=None):
        seen.update(platform=platform, account_id=account_id)
        return original_create(platform, data, display_name, account_id)

    monkeypatch.setattr(qr_sessions, "create", capture)
    headers = _key(client)
    response = client.post(
        "/api/skills/accounts/qr-login?platform=bilibili&account_id=42", headers=headers
    )
    assert response.status_code == 200, response.text
    assert seen == {"platform": "bilibili", "account_id": 42}
