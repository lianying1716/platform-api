"""升级演练（防回归）：模拟"旧版本数据 → 升级到 head"，断言数据与版本完好。

对应设计 §5 验收标准 2 与 Task 7。在真实 PostgreSQL 上用临时库执行，不碰开发/生产库。
覆盖：全链升级成功、已有数据保留、revision 收口到 head、版本门禁判定同步。
"""

import base64
import os
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
        pytest.skip(f"没有可用的 PostgreSQL，升级演练未验证: {exc}")
    admin.autocommit = True
    name = "platform_upg_" + uuid.uuid4().hex[:10]
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield f"postgresql+psycopg2://platform:platform@127.0.0.1:5433/{name}"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


@pytest.fixture(autouse=True)
def _configured_key(monkeypatch):
    """迁移过程会读加密密钥，显式提供避免被密钥检查挡住。"""
    from app.config import settings

    monkeypatch.setattr(settings, "credential_enc_key", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(settings, "credential_key_file", None)
    import app.core.crypto as crypto

    crypto._key = None
    yield
    crypto._key = None


def test_full_upgrade_preserves_account_data(temp_pg):
    """从 0011 升到 head：先写入账号数据，升级后数据仍在、revision 到达 head。"""
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "0011")

    engine = create_engine(temp_pg)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform_account (id, platform, display_name, external_id, "
                "credential_kind, status, status_message, created_at, updated_at) "
                "VALUES (1, 'bilibili', '演练账号', 'RID-1', 'cookie', 'active', '', now(), now())"
            )
        )

    # 升级到最新——这就是用户执行 `alembic upgrade head` 的等价动作。
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        row = conn.execute(
            text("SELECT display_name, external_id FROM platform_account WHERE id = 1")
        ).one()
    assert rev == "0019", "升级后应停在 head"
    assert row[0] == "演练账号" and row[1] == "RID-1", "已有账号数据必须保留"


def test_upgrade_is_idempotent(temp_pg):
    """重复执行 upgrade head（模拟容器重启）必须安全且不报错。"""
    cfg = _cfg(temp_pg)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # 第二次应为空操作
    with create_engine(temp_pg).connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert rev == "0019"


def test_version_guard_reports_synced_after_upgrade(temp_pg):
    """升级后版本门禁判定为 sync（契约 I3 的正向路径）。"""
    command.upgrade(_cfg(temp_pg), "head")
    from app.core import version_guard

    result = version_guard.check(db_url=temp_pg, auto_migrate=True, allow_unknown=False)
    assert result.ok is True
    assert result.action == "ok"


def test_version_guard_auto_migrates_behind_db(temp_pg):
    """门禁遇到落后库（0011）会自动迁移到 head（契约 I3 的"新代码配旧库"路径）。"""
    command.upgrade(_cfg(temp_pg), "0011")
    from app.core import version_guard

    result = version_guard.check(db_url=temp_pg, auto_migrate=True, allow_unknown=False)
    assert result.ok is True
    assert result.action == "migrated"
    with create_engine(temp_pg).connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert rev == "0019"
