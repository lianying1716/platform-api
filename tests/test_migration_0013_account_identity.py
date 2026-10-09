"""迁移 0013 的真实 PostgreSQL 验收：历史重复账号合并 + 部分唯一索引。"""

import uuid
from pathlib import Path

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


PG = dict(host="127.0.0.1", port=5433, user="platform", password="platform", dbname="postgres")


def _cfg(db_url: str) -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture()
def temp_pg():
    try:
        admin = psycopg2.connect(**PG)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"没有可用的 PostgreSQL，迁移未验证: {exc}")
    admin.autocommit = True
    name = "platform_mig13_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_migration_0013_merges_duplicate_accounts(temp_pg):
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0012")
    engine = create_engine(temp_pg)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform_account (id, platform, display_name, external_id, "
                "credential_kind, status, status_message, is_system, created_at, updated_at) VALUES "
                "(1, 'douyin', 'A', 'SEC_X', 'cookie', 'active', '', false, now(), now()),"
                "(2, 'douyin', 'A-dup', 'SEC_X', 'cookie', 'active', '', false, now(), now()),"
                "(3, 'douyin', 'no-id', '', 'cookie', 'active', '', false, now(), now()),"
                "(4, 'douyin', 'no-id-2', '', 'cookie', 'active', '', false, now(), now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO subscription (id, account_id, platform, source_type, remote_id, "
                "title, enabled, auto_download, interval_minutes, subdir_template, min_play, "
                "publish_after_days, max_items, only_new, download_quality, with_comments, created_at) "
                "VALUES (1, 2, 'douyin', 'favorite', 'f', 'F', true, false, 360, "
                "'{platform}/{creator}/{date}', 0, 0, 50, true, 'best', false, now())"
            )
        )

    command.upgrade(cfg, "0013")
    with engine.connect() as conn:
        accounts = conn.execute(
            text("SELECT id, external_id FROM platform_account ORDER BY id")
        ).all()
        assert accounts == [(1, "SEC_X"), (3, ""), (4, "")]
        assert conn.execute(text("SELECT account_id FROM subscription WHERE id=1")).scalar() == 1
        # 部分唯一索引生效
        with pytest.raises(Exception):
            conn.execute(
                text(
                    "INSERT INTO platform_account (platform, display_name, external_id, "
                    "credential_kind, status, status_message, is_system, created_at, updated_at) "
                    "VALUES ('douyin', 'dup', 'SEC_X', 'cookie', 'active', '', false, now(), now())"
                )
            )
            conn.commit()

    command.downgrade(cfg, "0012")
