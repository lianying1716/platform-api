"""Migration 0027: dedup key backfill, legacy duplicate collapse, downgrade."""

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
    name = "platform_mig27_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_migration_0027_backfills_and_collapses_active_duplicates(temp_pg):
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0026")
    engine = create_engine(temp_pg)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO job_queue (id, job_type, payload, status, priority, attempts, "
            "max_attempts, error, run_after, created_at, updated_at) VALUES "
            "(1, 'download', '{\"asset_id\": 1}', 'queued', 100, 0, 3, '', now(), now(), now()),"
            "(2, 'download', '{\"asset_id\": 1}', 'running', 100, 1, 3, '', now(), now(), now()),"
            "(3, 'download', '{\"asset_id\": 2}', 'done', 100, 1, 3, '', now(), now(), now())"
        ))

    command.upgrade(cfg, "0027")
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, status, dedup_key, error FROM job_queue ORDER BY id"
        )).mappings().all()
        index = conn.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname='uq_job_queue_active_dedup_key'"
        )).scalar()
    assert rows[0]["status"] == "queued"
    assert rows[0]["dedup_key"]
    assert rows[1]["status"] == "error"
    assert rows[1]["dedup_key"] is None
    assert "0027" in (rows[1]["error"] or "")
    assert rows[2]["dedup_key"] is None
    assert index and "WHERE" in index and "queued" in index

    command.downgrade(cfg, "0026")
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name='job_queue'"
            )).all()
        }
        indexes = {
            row[0]
            for row in conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE tablename='job_queue'"
            )).all()
        }
    assert "dedup_key" not in cols
    assert "uq_job_queue_active_dedup_key" not in indexes
