"""Migration 0024: storage lifecycle columns and FK survive a real upgrade."""

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
    name = "platform_mig24_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_migration_0024_adds_lifecycle_columns_and_restrict(temp_pg):
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0024")
    engine = create_engine(temp_pg)
    with engine.connect() as conn:
        media_cols = {
            row[0]
            for row in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='media_asset'")
            )
        }
        audit_cols = {
            row[0]
            for row in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='deletion_audit'")
            )
        }
        fk_delete = conn.execute(
            text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conname='fk_media_asset_storage_root'"
            )
        ).scalar()
    assert "deleted_at" in media_cols
    assert {"status", "error", "finished_at"} <= audit_cols
    assert fk_delete == "r"

    command.downgrade(cfg, "0023")
    with engine.connect() as conn:
        media_cols = {
            row[0]
            for row in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='media_asset'")
            )
        }
    assert "deleted_at" not in media_cols
