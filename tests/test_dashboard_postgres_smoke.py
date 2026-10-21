"""Opt-in PostgreSQL smoke for dashboard aggregation.

SQLite accepts the row_number/join/group-by query, but production uses
PostgreSQL.  This test runs the real endpoint against a disposable database so
type handling, NULL ordering and conditional aggregation are exercised there.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def pg_smoke():
    base_url = os.environ.get("XSAI_TEST_PG_URL", "").strip()
    if not base_url:
        pytest.skip("set XSAI_TEST_PG_URL to run the PostgreSQL dashboard smoke")
    parsed = make_url(base_url)
    # XSAI_TEST_PG_URL is also used by the migration test as the database under
    # test.  Derive a uniquely named disposable database from it here instead of
    # dropping or recreating the caller's database.
    parsed = parsed.set(database=f"xsai_dash_{uuid.uuid4().hex[:10]}")
    url = parsed.render_as_string(hide_password=False)
    admin_url = parsed.set(database="postgres")
    try:
        admin = create_engine(admin_url)
        with admin.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable or database not disposable: {exc}")
    # PostgreSQL cannot drop a database while a pooled connection to it is
    # alive.  The API's per-request sessions are closed, but SQLAlchemy keeps
    # idle connections unless NullPool is used.
    engine = create_engine(url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            with admin.connect() as conn:
                conn.execution_options(isolation_level="AUTOCOMMIT")
                conn.execute(text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
        finally:
            admin.dispose()


def test_dashboard_aggregation_on_real_postgres(pg_smoke, monkeypatch):
    from app.db import Base
    from app.main import app
    from app.models import (
        ContentItem,
        ContentMetricSnapshot,
        PlatformAccount,
    )
    import app.main as main_mod
    import app.db as db_mod

    Base.metadata.create_all(pg_smoke)
    Session = sessionmaker(bind=pg_smoke, autoflush=False, expire_on_commit=False)
    seed = Session()
    account = PlatformAccount(platform="douyin", display_name="pg", status="active")
    seed.add(account)
    seed.flush()
    video = ContentItem(
        account_id=account.id, platform="douyin", external_id="v", content_type="video",
        title="video", author_name="a",
    )
    unknown = ContentItem(
        account_id=account.id, platform="douyin", external_id="u", content_type="video",
        title="unknown", author_name="a",
    )
    article = ContentItem(
        account_id=account.id, platform="douyin", external_id="a", content_type="article",
        title="article", author_name="a",
    )
    seed.add_all([video, unknown, article])
    seed.flush()
    now = datetime.utcnow()
    seed.add_all(
        [
            ContentMetricSnapshot(content_id=video.id, play=100, like=10, captured_at=now - timedelta(hours=1)),
            ContentMetricSnapshot(content_id=video.id, play=150, like=12, captured_at=now),
            ContentMetricSnapshot(content_id=unknown.id, play=None, like=3, captured_at=now),
            ContentMetricSnapshot(content_id=article.id, play=999, like=4, captured_at=now),
        ]
    )
    seed.commit()
    seed.close()

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(main_mod, "init_db", lambda: None)
    monkeypatch.setattr(main_mod, "start_scheduler", lambda: None)
    monkeypatch.setattr(main_mod, "stop_scheduler", lambda: None)
    app.dependency_overrides[db_mod.get_db] = override
    try:
        with TestClient(app) as client:
            client.post("/api/auth/setup", json={"password": "test1234"})
            token = client.post("/api/auth/login", json={"password": "test1234"}).json()["token"]
            body = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"}).json()
    finally:
        app.dependency_overrides.clear()

    assert body["metric_coverage"]["play"] == {"known": 1, "total": 2}
    assert body["engagement"]["play"] == 150
    assert body["hot"] == [{"title": "video", "platform": "douyin", "author": "a", "play": 150}]
    douyin = next(p for p in body["platforms"] if p["platform"] == "douyin")
    assert douyin["metric_coverage"]["play"] == {"known": 1, "total": 2}
    assert douyin["play"] == 150
