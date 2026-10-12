"""迁移 0018 的 SQLite 验收：新表、扩列、明文中继与残缺旧配置分支。"""

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
    return "sqlite:///" + str(tmp_path / "mig18.db")


def _columns(engine, table: str) -> dict:
    return {col["name"]: col for col in inspect(engine).get_columns(table)}


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


@pytest.fixture(autouse=True)
def _configured_key(monkeypatch, tmp_path):
    """测试迁移时显式提供加密密钥，保证结构测试不被密钥检查挡住。"""
    from app.config import settings

    monkeypatch.setattr(settings, "credential_enc_key", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(settings, "credential_key_file", None)
    import app.core.crypto as crypto

    crypto._key = None
    yield
    crypto._key = None


def test_migration_0018_creates_tables_and_columns(tmp_path):
    url = _sqlite_url(tmp_path)
    command.upgrade(_cfg(url), "0018")
    engine = create_engine(url)
    try:
        assert {"ai_provider", "ai_model", "model_assignment", "model_call_attempt"} <= _tables(engine)
        for table in ("analysis_result", "video_understanding", "viral_breakdown"):
            cols = _columns(engine, table)
            assert "routing" in cols
            assert cols["model"]["type"].length == 200
        assert "finished_at" in _columns(engine, "model_call_attempt")
    finally:
        engine.dispose()


def test_migration_0018_downgrade_clean(tmp_path):
    url = _sqlite_url(tmp_path)
    cfg = _cfg(url)
    command.upgrade(cfg, "0018")
    command.downgrade(cfg, "0017")
    engine = create_engine(url)
    try:
        assert "ai_provider" not in _tables(engine)
        for table in ("analysis_result", "video_understanding", "viral_breakdown"):
            assert "routing" not in _columns(engine, table)
    finally:
        engine.dispose()


def test_migration_0011_downgrade_sqlite_safe(tmp_path):
    """0011 downgrade 在 SQLite 也必须可执行（JSON/CURRENT_TIMESTAMP 方言兼容）。"""
    url = _sqlite_url(tmp_path)
    cfg = _cfg(url)
    command.upgrade(cfg, "0011")
    command.downgrade(cfg, "0010")
    engine = create_engine(url)
    try:
        assert "followed_creator" in _tables(engine)
    finally:
        engine.dispose()


def test_migration_0018_full_legacy_config_migrates_and_removes_plaintext(tmp_path):
    url = _sqlite_url(tmp_path)
    cfg = _cfg(url)
    command.upgrade(cfg, "0017")
    engine = create_engine(url)
    with engine.begin() as conn:
        for key, value in {
            "llm_base_url": "https://api.example.com/v1",
            "llm_api_key": "sk-legacy-secret",
            "llm_model": "legacy-model",
        }.items():
            conn.execute(
                text(
                    "INSERT INTO system_setting (key, value, updated_at) "
                    "VALUES (:k, :v, CURRENT_TIMESTAMP)"
                ),
                {"k": key, "v": value},
            )
    engine.dispose()

    command.upgrade(cfg, "0018")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            provider = conn.execute(text("SELECT * FROM ai_provider")).mappings().one()
            assert provider["base_url"] == "https://api.example.com/v1"
            assert provider["api_key_encrypted"] and "sk-legacy-secret" not in provider["api_key_encrypted"]
            model = conn.execute(
                text("SELECT * FROM ai_model WHERE model_key = 'legacy-model'")
            ).mappings().one()
            assert model["model_key"] == "legacy-model"
            assignment = conn.execute(
                text("SELECT * FROM model_assignment WHERE work_type = 'text_analysis'")
            ).mappings().one()
            assert assignment["primary_model_id"] == model["id"]
            assert assignment["legacy_pending_validation"] in (1, True)
            keys = dict(conn.execute(text("SELECT key, value FROM system_setting")).all())
            assert "llm_api_key" not in keys
            assert keys.get("model_access_legacy_imported") == "1"
    finally:
        engine.dispose()


def test_migration_0018_missing_key_does_not_take_over(tmp_path, monkeypatch):
    url = _sqlite_url(tmp_path)
    cfg = _cfg(url)
    command.upgrade(cfg, "0017")
    from app.config import settings

    monkeypatch.setattr(settings, "credential_enc_key", "")
    engine = create_engine(url)
    with engine.begin() as conn:
        for key, value in {
            "llm_base_url": "https://api.example.com/v1",
            "llm_model": "legacy-model",
        }.items():
            conn.execute(
                text(
                    "INSERT INTO system_setting (key, value, updated_at) "
                    "VALUES (:k, :v, CURRENT_TIMESTAMP)"
                ),
                {"k": key, "v": value},
            )
    engine.dispose()

    command.upgrade(cfg, "0018")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM model_assignment")).scalar() == 0
            keys = dict(conn.execute(text("SELECT key, value FROM system_setting")).all())
            assert "model_access_legacy_imported" not in keys
    finally:
        engine.dispose()


def test_migration_0018_process_defaults_used_for_classify_and_vision(tmp_path):
    """process_classify_model / process_vision_model 未显式落库时按内置默认 qwen3.8-flash 迁移。"""
    url = _sqlite_url(tmp_path)
    cfg = _cfg(url)
    command.upgrade(cfg, "0017")
    engine = create_engine(url)
    with engine.begin() as conn:
        for key, value in {
            "llm_base_url": "https://api.example.com/v1",
            "llm_api_key": "sk-legacy-secret",
            "llm_model": "legacy-model",
        }.items():
            conn.execute(
                text(
                    "INSERT INTO system_setting (key, value, updated_at) "
                    "VALUES (:k, :v, CURRENT_TIMESTAMP)"
                ),
                {"k": key, "v": value},
            )
    engine.dispose()
    command.upgrade(cfg, "0018")

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            keys = {row[0] for row in conn.execute(text("SELECT model_key FROM ai_model")).all()}
            assert "qwen3.8-flash" in keys
            work_types = {
                row[0]
                for row in conn.execute(
                    text("SELECT work_type FROM model_assignment WHERE primary_model_id IS NOT NULL")
                ).all()
            }
            assert {"text_analysis", "cover_classify", "video_understanding"} <= work_types
    finally:
        engine.dispose()
