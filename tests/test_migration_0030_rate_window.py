"""Migration 0030 and the PostgreSQL UPSERT used by API key limiting."""

import uuid
from pathlib import Path

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


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
    name = "platform_mig30_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_migration_0030_creates_rate_window_and_cascades(temp_pg):
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0030")
    engine = create_engine(temp_pg)
    with engine.begin() as conn:
        key_id = conn.execute(text(
            "INSERT INTO api_key (name, key_hash, prefix, scopes, rate_limit_per_min, enabled, created_at) "
            "VALUES ('t', 'hash', 'sk-t', '[\"read\"]', 2, true, now()) RETURNING id"
        )).scalar_one()
        conn.execute(text(
            "INSERT INTO api_rate_window (key_id, window_start, count, updated_at) "
            "VALUES (:key, date_trunc('minute', now()), 1, now())"
        ), {"key": key_id})

    with engine.begin() as conn:
        stmt = text(
            "INSERT INTO api_rate_window (key_id, window_start, count, updated_at) "
            "VALUES (:key, date_trunc('minute', now()), 1, now()) "
            "ON CONFLICT (key_id, window_start) DO UPDATE "
            "SET count = api_rate_window.count + 1, updated_at = excluded.updated_at "
            "WHERE api_rate_window.count < :limit"
        )
        assert conn.execute(stmt, {"key": key_id, "limit": 2}).rowcount == 1
        assert conn.execute(stmt, {"key": key_id, "limit": 2}).rowcount == 0
        count = conn.execute(text("SELECT count FROM api_rate_window WHERE key_id = :k"), {"k": key_id}).scalar_one()
    assert count == 2

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM api_key WHERE id = :k"), {"k": key_id})
        remaining = conn.execute(text("SELECT count(*) FROM api_rate_window")).scalar_one()
    assert remaining == 0

    command.downgrade(cfg, "0029")
    engine.dispose()
