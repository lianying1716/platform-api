"""迁移互斥（契约 I2）：SQLite 自动跳过，PG 用 advisory lock 串行化。

对应设计 3.3。核心：两个进程同时迁移时只有一个真正执行，其余等待/超时失败。
"""

import uuid
from pathlib import Path

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.migration_lock import LOCK_KEY, migration_lock


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
        pytest.skip(f"没有可用的 PostgreSQL，迁移锁未验证: {exc}")
    admin.autocommit = True
    name = "platform_lock_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_sqlite_migration_not_blocked(tmp_path):
    """SQLite 不支持 pg advisory lock，迁移必须照常成功（自动跳过锁）。"""
    url = "sqlite:///" + str(tmp_path / "lock.db")
    command.upgrade(_cfg(url), "head")  # 不抛异常即通过
    with create_engine(url).connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert rev == "0019"


def test_pg_full_migration_succeeds(temp_pg):
    """全新 PG 库升级到 head 成功——证明加锁没有破坏正常迁移。"""
    command.upgrade(_cfg(temp_pg), "head")
    with create_engine(temp_pg).connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert rev == "0019"


def test_pg_lock_serializes_second_holder(temp_pg):
    """连接 A 持锁时，连接 B 以极短超时抢锁必须失败；A 释放后 B 能拿到。

    这直接证明"两个进程并发迁移只有一个能进"——Grafana boot loop 的根因。
    """
    engine = create_engine(temp_pg)
    conn_a = engine.connect()
    conn_b = engine.connect()
    try:
        with migration_lock(conn_a, timeout_seconds=5):
            # B 以 1s 超时抢锁，A 未释放，必须抛错而不是无限等待。
            with pytest.raises(Exception):
                with migration_lock(conn_b, timeout_seconds=1):
                    pass
        # A 释放后 B 能正常拿到锁。
        with migration_lock(conn_b, timeout_seconds=5):
            pass
    finally:
        conn_a.close()
        conn_b.close()
        engine.dispose()


def test_pg_lock_reentrant_after_release(temp_pg):
    """同一连接重复进出锁不得残留（确认 unlock 到位）。"""
    engine = create_engine(temp_pg)
    conn = engine.connect()
    try:
        for _ in range(3):
            with migration_lock(conn, timeout_seconds=5):
                pass
        # 结束后锁应已释放：另一连接可立即获取。
        other = engine.connect()
        try:
            with migration_lock(other, timeout_seconds=3):
                pass
        finally:
            other.close()
    finally:
        conn.close()
        engine.dispose()


def test_pg_lock_timeout_restored_after_release(temp_pg):
    """锁退出后 lock_timeout 必须复位，不能污染复用连接上的后续查询。"""
    engine = create_engine(temp_pg)
    conn = engine.connect()
    try:
        before = conn.execute(text("SHOW lock_timeout")).scalar()
        with migration_lock(conn, timeout_seconds=7):
            assert conn.execute(text("SHOW lock_timeout")).scalar() == "7s"
        after = conn.execute(text("SHOW lock_timeout")).scalar()
        assert after == before, f"lock_timeout 未复位：{before} -> {after}"
    finally:
        conn.close()
        engine.dispose()
