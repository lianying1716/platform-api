"""Migration 0025: nullable tri-state fields and comment-mode backfill."""

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
    name = "platform_mig25_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_migration_0025_backfills_comment_modes_and_keeps_nullable_tristate(temp_pg):
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0024")
    engine = create_engine(temp_pg)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO storage_root (id, name, path, is_default, created_at) "
            "VALUES (1, 'r', '/tmp/r', true, now())"
        ))
        conn.execute(text(
            "INSERT INTO platform_account (id, platform, display_name, external_id, credential_kind, status, "
            "status_message, created_at, updated_at) VALUES "
            "(1, 'douyin', 'A', 'A', 'cookie', 'active', '', now(), now())"
        ))
        conn.execute(text(
            "INSERT INTO subscription (id, account_id, platform, source_type, remote_id, title, enabled, "
            "auto_download, interval_minutes, subdir_template, min_play, publish_after_days, max_items, "
            "only_new, download_quality, with_comments, created_at) VALUES "
            "(1, 1, 'douyin', 'favorite', 'f1', '', true, false, 360, '{platform}', 0, 0, 50, true, "
            "'best', true, now()),"
            "(2, 1, 'douyin', 'favorite', 'f2', '', true, false, 360, '{platform}', 0, 0, 50, true, "
            "'best', false, now())"
        ))
        conn.execute(text(
            "INSERT INTO crawl_task (id, platform, keyword, sort, limit_per_run, with_comments, "
            "to_candidate, min_play, interval_minutes, enabled, created_at) VALUES "
            "(1, 'douyin', 'a', 'popular', 50, true, true, 0, 1440, true, now()),"
            "(2, 'douyin', 'b', 'popular', 50, false, true, 0, 1440, true, now())"
        ))

    command.upgrade(cfg, "0025")
    with engine.connect() as conn:
        modes = dict(conn.execute(text("SELECT id, comment_mode FROM subscription ORDER BY id")).all())
        crawl_modes = dict(conn.execute(text("SELECT id, comment_mode FROM crawl_task ORDER BY id")).all())
        nullability = {
            row[0]: row[1]
            for row in conn.execute(text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name='subscription' AND column_name IN "
                "('interval_minutes','auto_transcribe','auto_analyze')"
            )).all()
        }
        has_last_attempt = conn.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='crawl_task' AND column_name='last_attempt_at'"
        )).scalar()
    assert modes == {1: "force", 2: "gate"}
    assert crawl_modes == {1: "force", 2: "off"}
    assert nullability == {
        "interval_minutes": "YES",
        "auto_transcribe": "YES",
        "auto_analyze": "YES",
    }
    assert has_last_attempt == 1

    command.downgrade(cfg, "0024")
    with engine.connect() as conn:
        cols = {
            row[0] for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name='subscription'"
            )).all()
        }
    assert "with_comments" in cols
    assert "comment_mode" not in cols
