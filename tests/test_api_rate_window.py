"""SEC-06: API key limits must survive a process restart and be atomic."""

from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core import api_auth
from app.models import ApiKey, ApiRateWindow


def _key(session, limit=3) -> ApiKey:
    row = ApiKey(name="limits", key_hash="h" * 64, scopes=["read"], rate_limit_per_min=limit)
    session.add(row)
    session.commit()
    return row


def test_window_survives_new_session(session, _engine):
    """A fresh session (simulating a restarted process) still sees the count."""
    from sqlalchemy.orm import sessionmaker

    key = _key(session, limit=2)
    now = datetime(2026, 10, 19, 12, 0, 1)
    assert api_auth.consume_rate_slot(session, key.id, 2, now=now) is True
    assert api_auth.consume_rate_slot(session, key.id, 2, now=now) is True
    assert api_auth.consume_rate_slot(session, key.id, 2, now=now) is False

    Restarted = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    fresh = Restarted()
    try:
        # Same minute, new process: still limited.
        assert api_auth.consume_rate_slot(fresh, key.id, 2, now=now + timedelta(seconds=30)) is False
    finally:
        fresh.close()


def test_next_minute_gets_a_fresh_budget(session):
    key = _key(session, limit=1)
    now = datetime(2026, 10, 19, 12, 0, 1)
    assert api_auth.consume_rate_slot(session, key.id, 1, now=now) is True
    assert api_auth.consume_rate_slot(session, key.id, 1, now=now) is False
    assert api_auth.consume_rate_slot(session, key.id, 1, now=now + timedelta(minutes=1)) is True


def test_counter_never_exceeds_limit_under_concurrency(_engine):
    """Many independent sessions racing the same minute must not overspend."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    setup = Session()
    try:
        key = _key(setup, limit=5)
        key_id = key.id
    finally:
        setup.close()

    now = datetime(2026, 10, 19, 12, 0, 1)
    accepted = 0
    sessions = []
    try:
        for _ in range(12):
            db = Session()
            sessions.append(db)
            if api_auth.consume_rate_slot(db, key_id, 5, now=now):
                accepted += 1
    finally:
        for db in sessions:
            db.close()

    check = Session()
    try:
        count = check.scalar(select(ApiRateWindow.count).where(ApiRateWindow.key_id == key_id))
    finally:
        check.close()
    assert accepted == 5
    assert count == 5


def test_expired_windows_are_pruned(session):
    from app.services.foundation import purge_old_task_logs

    key = _key(session, limit=1)
    old = datetime.utcnow() - timedelta(days=31)
    session.add(ApiRateWindow(key_id=key.id, window_start=old, count=1))
    session.add(ApiRateWindow(key_id=key.id, window_start=datetime.utcnow(), count=1))
    session.commit()

    _, _, pruned = purge_old_task_logs(session, older_than_days=30)

    assert pruned == 1
    remaining = session.scalar(select(func.count()).select_from(ApiRateWindow))
    assert remaining == 1
