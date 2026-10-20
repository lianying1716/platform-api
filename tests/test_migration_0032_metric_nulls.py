"""Migration 0032: metric columns accept NULL without losing existing values."""

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
    name = "platform_mig32_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_metric_null_round_trip(temp_pg):
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0032")
    engine = create_engine(temp_pg)
    with engine.begin() as conn:
        nullable = {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'content_metric_snapshot'"
                )
            )
        }
    assert all(nullable[name] == "YES" for name in ("play", "like", "comment", "favorite", "share"))

    command.downgrade(cfg, "0031")
    with engine.connect() as conn:
        nullable = {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'content_metric_snapshot'"
                )
            )
        }
    assert all(nullable[name] == "NO" for name in ("play", "like", "comment", "favorite", "share"))
