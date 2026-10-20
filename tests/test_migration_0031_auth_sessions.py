"""Migration 0031: persisted sessions and login-attempt windows."""

import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


PG = dict(host="127.0.0.1", port=5433, user="platform", password="platform", dbname="postgres")


def _cfg(url: str) -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture()
def temp_pg():
    try:
        admin = psycopg2.connect(**PG)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"没有可用的 PostgreSQL: {exc}")
    admin.autocommit = True
    name = "platform_mig31_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_migration_0031_round_trip(temp_pg):
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0031")
    engine = create_engine(temp_pg)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO admin_session (token_hash, expires_at, revoked_at) "
                "VALUES (:h, now() + interval '7 days', NULL)"
            ),
            {"h": "a" * 64},
        )
        conn.execute(
            text("INSERT INTO auth_rate_event (key, created_at) VALUES ('global', now())")
        )
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM admin_session")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM auth_rate_event")).scalar_one() == 1

    command.downgrade(cfg, "0030")
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('admin_session', 'auth_rate_event')"
            )
        ).scalar_one()
    assert exists == 0


def test_sliding_window_is_atomic_under_postgres_concurrency(temp_pg):
    """Ten simultaneous transactions may not exceed a five-attempt budget."""
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0031")
    engine = create_engine(temp_pg)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    from datetime import datetime

    from app.core import security

    now = datetime(2026, 10, 20, 12, 0, 0)

    def attempt(_index: int) -> bool:
        db = Session()
        try:
            allowed, _retry = security.consume_login_attempt(db, "198.51.100.88", now=now)
            return allowed
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))
    assert sum(results) == 5
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT key, count(*) FROM auth_rate_event GROUP BY key ORDER BY key")
        ).all()
    # Each allowed attempt records one IP event and one global event.
    assert rows == [("global", 5), (rows[1][0], 5)]
    assert rows[1][0].startswith("ip:")
    engine.dispose()
