"""Logout and password changes must revoke both Bearer and Cookie sessions."""

from app.core import security
from app.models import SystemSetting


def _token(client, password="test1234"):
    response = client.post("/api/auth/login", json={"password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_logout_revokes_bearer_token(client):
    token = _token(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/accounts", headers=headers).status_code == 200

    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/accounts", headers=headers).status_code == 401


def test_logout_revokes_cookie_session(client):
    response = client.post("/api/auth/login", json={"password": "test1234"})
    assert response.status_code == 200
    client.cookies.clear()
    cookie = response.cookies.get(security.SESSION_COOKIE)
    assert cookie
    client.cookies.set(security.SESSION_COOKIE, cookie)
    assert client.get("/api/accounts").status_code == 200

    assert client.post("/api/auth/logout").status_code == 200
    # Simulate a browser that keeps a stale cookie despite receiving Set-Cookie deletion.
    client.cookies.set(security.SESSION_COOKIE, cookie)
    assert client.get("/api/accounts").status_code == 401


def test_password_change_revokes_old_bearer_and_cookie(client, session):
    first = client.post("/api/auth/login", json={"password": "test1234"})
    old_token = first.json()["token"]
    old_cookie = first.cookies.get(security.SESSION_COOKIE)
    assert old_cookie

    changed = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {old_token}"},
        json={"old_password": "test1234", "new_password": "newtest1234"},
    )
    assert changed.status_code == 200, changed.text

    assert client.get("/api/accounts", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    client.cookies.clear()
    client.cookies.set(security.SESSION_COOKIE, old_cookie)
    assert client.get("/api/accounts").status_code == 401

    # A fresh login under the new password works and receives a token in the new epoch.
    new_token = _token(client, "newtest1234")
    assert client.get("/api/accounts", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    assert session.get(SystemSetting, security.SESSION_EPOCH_KEY) is not None


def test_password_change_increments_epoch_and_old_epoch_token_is_rejected(client, session):
    old_token = _token(client)
    epoch_row = session.get(SystemSetting, security.SESSION_EPOCH_KEY)
    before = int(epoch_row.value) if epoch_row else 0
    changed = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {old_token}"},
        json={"old_password": "test1234", "new_password": "newtest1234"},
    )
    assert changed.status_code == 200, changed.text
    session.expire_all()
    after = int(session.get(SystemSetting, security.SESSION_EPOCH_KEY).value)
    assert after == before + 1
    assert client.get("/api/accounts", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert client.get(
        "/api/accounts",
        headers={"Authorization": f"Bearer {_token(client, 'newtest1234')}"},
    ).status_code == 200
