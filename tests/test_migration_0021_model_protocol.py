"""迁移 0021：ai_model 增加模型级协议列（protocol / protocol_source）。

只加列、不回填：存量行 protocol 为 NULL（= 继承供应商）、protocol_source 为
'inherit'，升级前后行为一致。
"""

import base64
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _cfg(db_url: str) -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _sqlite_url(tmp_path: Path) -> str:
    return "sqlite:///" + str(tmp_path / "mig21.db")


def _columns(engine, table: str) -> set[str]:
    return {col["name"] for col in inspect(engine).get_columns(table)}


@pytest.fixture(autouse=True)
def _configured_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "credential_enc_key", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(settings, "credential_key_file", None)
    import app.core.crypto as crypto

    crypto._key = None
    yield
    crypto._key = None


def test_migration_0021_adds_model_protocol_columns(tmp_path):
    url = _sqlite_url(tmp_path)
    command.upgrade(_cfg(url), "0020")
    engine = create_engine(url)
    try:
        before = _columns(engine, "ai_model")
        assert "protocol" not in before and "protocol_source" not in before
    finally:
        engine.dispose()

    command.upgrade(_cfg(url), "0021")
    engine = create_engine(url)
    try:
        assert {"protocol", "protocol_source"} <= _columns(engine, "ai_model")
    finally:
        engine.dispose()


def test_migration_0021_existing_rows_default_to_inherit(tmp_path):
    """存量模型升级后 protocol 为空、protocol_source='inherit'（跟随供应商）。"""
    url = _sqlite_url(tmp_path)
    command.upgrade(_cfg(url), "0020")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ai_provider (id, name, preset_key, protocol, base_url, "
                    "credential_source, config_revision, enabled, last_test_status, last_test_error, "
                    "created_at, updated_at) "
                    "VALUES (1, 'p', 'custom', 'openai', 'https://x/v1', 'api_key', 1, true, '', '', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO ai_model (id, provider_id, model_key, display_name, import_source, "
                    "enabled, capabilities, probe_status, probe_error, created_at, updated_at) "
                    "VALUES (1, 1, 'm', 'm', 'discovered', true, '{}', 'untested', '', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(_cfg(url), "0021")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT protocol, protocol_source FROM ai_model WHERE id = 1")
            ).one()
        assert row[0] is None
        assert row[1] == "inherit"
    finally:
        engine.dispose()


def test_migration_0021_downgrade_clean(tmp_path):
    url = _sqlite_url(tmp_path)
    command.upgrade(_cfg(url), "0021")
    command.downgrade(_cfg(url), "0020")
    engine = create_engine(url)
    try:
        cols = _columns(engine, "ai_model")
        assert "protocol" not in cols and "protocol_source" not in cols
    finally:
        engine.dispose()
