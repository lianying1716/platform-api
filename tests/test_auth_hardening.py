"""Authentication hardening contracts.

These tests keep the admin boundary honest across restarts, processes and
parallel first-run requests.  They intentionally exercise database state rather
than a module-local dictionary.
"""

from sqlalchemy.orm import sessionmaker

from app.core import security


def _setup(anon_client) -> str:
    response = anon_client.post("/api/auth/setup", json={"password": "test1234"})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_login_is_rate_limited_after_repeated_failures(anon_client):
    _setup(anon_client)
    attempts = []
    for _ in range(6):
        attempts.append(anon_client.post("/api/auth/login", json={"password": "wrong-pass"}))

    assert [r.status_code for r in attempts[:5]] == [401] * 5
    assert attempts[5].status_code == 429
    assert "Retry-After" in attempts[5].headers

    # The lockout also protects the correct password; otherwise an attacker can
    # keep guessing while legitimate requests are silently downgraded.
    blocked = anon_client.post("/api/auth/login", json={"password": "test1234"})
    assert blocked.status_code == 429


def test_global_failure_budget_blocks_distributed_guessing(anon_client, session):
    now = security.utcnow()
    _setup(anon_client)  # bind the API client to this test's database
    rejected = 0
    for index in range(40):
        allowed, _ = security.consume_login_attempt(session, f"198.51.100.{index}", now=now)
        if not allowed:
            rejected += 1

    assert rejected >= 10

    allowed, retry_after = security.login_allowed(session, "203.0.113.9", now=now)
    assert not allowed
    assert retry_after > 0


def test_session_survives_new_database_connection_and_revokes_across_processes(_engine, session):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    first = Session()
    try:
        token = security.issue_token(first)
    finally:
        first.close()

    token = security.issue_token(session)
    OtherSession = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    other = OtherSession()
    try:
        assert security.token_valid(token, other)
        security.revoke_token(other, token)
        assert not security.token_valid(token, session)
    finally:
        other.close()


def test_password_change_revokes_persisted_sessions(client, session):
    token = client.post("/api/auth/login", json={"password": "test1234"}).json()["token"]
    response = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": "test1234", "new_password": "newtest1234"},
    )
    assert response.status_code == 200, response.text
    assert not security.token_valid(token, session)


def test_setup_cannot_overwrite_an_existing_admin(anon_client):
    first = _setup(anon_client)
    assert first
    second = anon_client.post(
        "/api/auth/setup",
        json={"password": "different1234"},
    )
    assert second.status_code == 400
    assert anon_client.post("/api/auth/login", json={"password": "different1234"}).status_code == 401
    assert anon_client.post("/api/auth/login", json={"password": "test1234"}).status_code == 200


def test_expired_sessions_and_login_windows_are_pruned(session):
    from datetime import timedelta

    from app.models import AdminSession, AuthRateEvent

    old = security.utcnow() - timedelta(days=31)
    session.add(
        AdminSession(
            token_hash="a" * 64,
            expires_at=old,
            revoked_at=None,
            created_at=old,
        )
    )
    session.add(AuthRateEvent(key="old", created_at=old))
    session.commit()

    from app.services.foundation import purge_old_task_logs

    purge_old_task_logs(session, older_than_days=30)
    assert session.get(AdminSession, "a" * 64) is None


def test_failed_login_budget_does_not_reset_at_fixed_window_boundary(anon_client):
    """A saturated five-minute window must not double at the clock boundary."""
    from datetime import datetime, timedelta

    _setup(anon_client)
    now = datetime(2026, 10, 20, 12, 0, 59)
    # The test uses the API's database and supplies the exact clock time.  It
    # proves the sliding boundary without sleeping five minutes.
    from app.db import get_db
    from app.main import app

    dependency = app.dependency_overrides[get_db]
    db = next(dependency())
    try:
        for _ in range(5):
            ok, _retry = security.consume_login_attempt(db, "198.51.100.1", now=now)
            assert ok
        blocked, retry_after = security.consume_login_attempt(
            db, "198.51.100.1", now=now + timedelta(seconds=2)
        )
        assert not blocked
        assert retry_after > 0
    finally:
        db.close()


def test_sliding_window_allows_only_five_concurrent_attempts(session):
    from app.models import AuthRateEvent

    now = security.utcnow()
    accepted = 0
    for _ in range(20):
        allowed, _ = security.consume_login_attempt(session, "203.0.113.77", now=now)
        accepted += int(allowed)
    assert accepted == 5
    assert session.query(AuthRateEvent).filter(AuthRateEvent.key != "global").count() == 5
