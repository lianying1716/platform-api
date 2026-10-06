"""queue.enqueue 的 PostgreSQL 冒烟测试（dedup 去重路径）。

背景：`job_queue.payload` 在 PG 上是 `json` 类型，而 `json` 没有 `=` 运算符（只有 `jsonb` 有）。
旧实现用 ORM 等值比较 `JobQueue.payload == payload` 做去重，会在 PG 上抛
`operator does not exist: json = json`；SQLite 的 JSON 支持 `=`，所以 `tests/` 里的
SQLite 用例（`conftest.py` 只跑 SQLite）发现不了这个差异。

本用例在**真实 PostgreSQL** 上验证 enqueue 的去重语义。默认连本地开发库
（`127.0.0.1:5433`）；无 psycopg2 或连不上时自动 skip，保证裸跑 `uv run pytest` 仍全绿。
可用 `PLATFORM_PG_TEST_URL` 覆盖为可建库的 PG 实例。
"""

import os

import pytest

pytest.importorskip("psycopg2")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

PG_ADMIN_URL = os.environ.get(
    "PLATFORM_PG_TEST_URL",
    "postgresql+psycopg2://platform:platform@127.0.0.1:5433/platform",
)
SCRATCH_DB = "platform_queue_smoke"


def _base_url() -> str:
    """去掉末尾库名，保留 `...user:pw@host:port/`。"""
    return PG_ADMIN_URL.rsplit("/", 1)[0] + "/"


def _pg_available() -> bool:
    try:
        eng = create_engine(PG_ADMIN_URL)
        try:
            with eng.connect() as conn:
                conn.execute(text("select 1"))
            return True
        finally:
            eng.dispose()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="本地无可用 PostgreSQL（设 PLATFORM_PG_TEST_URL 指向可建库实例）",
)


@pytest.fixture()
def pg_engine():
    """在真实 PG 上建一个 scratch 库并建全部表，用完删除。"""
    import app.models  # noqa: F401  注册全部模型
    from app.db import Base

    base = _base_url()
    scratch_url = base + SCRATCH_DB
    admin = create_engine(base + "postgres", isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB}"))
            conn.execute(text(f"CREATE DATABASE {SCRATCH_DB}"))
    except Exception as exc:  # 无建库权限等 → skip 而非 fail
        admin.dispose()
        pytest.skip(f"无法创建 scratch PG 库：{type(exc).__name__}: {exc}")

    engine = create_engine(scratch_url)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB}"))
        admin.dispose()


def _session(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_payload_column_is_json_on_pg(pg_engine):
    """证实触发条件：PG 上 payload 列确为无 `=` 运算符的 `json` 类型。"""
    with pg_engine.connect() as conn:
        dtype = conn.execute(
            text(
                "select data_type from information_schema.columns "
                "where table_name = 'job_queue' and column_name = 'payload'"
            )
        ).scalar()
    assert dtype == "json"


def test_enqueue_dedup_on_pg(pg_engine):
    """dedup=True 在 PG 上不得抛 json=json，且去重语义正确。"""
    from app.core.queue import complete, enqueue
    from app.models import JobQueue

    db = _session(pg_engine)
    try:
        # 首次入队（不应抛错）
        j1 = enqueue(db, "download", {"asset_id": 1})
        assert j1.id is not None

        # 相同 job_type+payload 的未完成任务 → 命中去重，返回同一行、不新增
        j2 = enqueue(db, "download", {"asset_id": 1})
        assert j2.id == j1.id
        assert db.query(JobQueue).filter_by(job_type="download").count() == 1

        # 不同 payload → 正常新增
        j3 = enqueue(db, "download", {"asset_id": 2})
        assert j3.id != j1.id
        assert db.query(JobQueue).filter_by(job_type="download").count() == 2

        # 已完成（done）任务不参与去重 → 同 payload 可再次入队
        complete(db, j1)
        j4 = enqueue(db, "download", {"asset_id": 1})
        assert j4.id != j1.id
        assert db.query(JobQueue).filter_by(job_type="download").count() == 3

        # 空 payload / 多键 payload 的相等判断走 Python 侧，语义与 SQLite 一致
        enqueue(db, "crawl", {})
        enqueue(db, "crawl", {})
        assert db.query(JobQueue).filter_by(job_type="crawl").count() == 1
    finally:
        db.close()
