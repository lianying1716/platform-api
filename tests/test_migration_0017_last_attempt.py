"""迁移 0017 的真实 PostgreSQL 验收：账号存储根 + 订阅自动加工开关可升级/降级。"""

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
    name = "platform_mig17_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t"
                ),
                {"t": table},
            )
        }


def test_migration_0017_adds_last_attempt_at(temp_pg):
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0017")
    engine = create_engine(temp_pg)
    assert "last_attempt_at" in _columns(engine, "subscription")

    # 降级必须干净地移除该列（迁移可回滚）。
    command.downgrade(cfg, "0016")
    assert "last_attempt_at" not in _columns(engine, "subscription")
