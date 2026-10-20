"""Migration 0028: durable event outbox and downgrade."""

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
    name = "platform_mig28_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_migration_0028_creates_event_outbox(temp_pg):
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0028")
    engine = create_engine(temp_pg)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO event_outbox (event, data) VALUES ('asset_status', '{\"asset_id\": 1}')"
        ))
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, event, data FROM event_outbox")).mappings().one()
        indexes = {
            r[0] for r in conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE tablename='event_outbox'"
            )).all()
        }
    assert row["event"] == "asset_status" and row["data"] == {"asset_id": 1}
    assert {"ix_event_outbox_created_at", "ix_event_outbox_event_id"} <= indexes

    command.downgrade(cfg, "0027")
    with engine.connect() as conn:
        exists = conn.execute(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='event_outbox'"
        )).scalar()
    assert exists == 0
