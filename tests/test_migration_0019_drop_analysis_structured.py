"""迁移 0019：确认 analysis_result.structured 被删除，且摘要/标签等保留。"""

import base64
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _cfg(db_url: str) -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _sqlite_url(tmp_path: Path) -> str:
    return "sqlite:///" + str(tmp_path / "mig19.db")


def _columns(engine, table: str) -> set[str]:
    return {col["name"] for col in inspect(engine).get_columns(table)}


@pytest.fixture(autouse=True)
def _configured_key(monkeypatch):
    """迁移会读加密密钥，显式提供避免被结构检查挡住。"""
    from app.config import settings

    monkeypatch.setattr(settings, "credential_enc_key", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(settings, "credential_key_file", None)
    import app.core.crypto as crypto

    crypto._key = None
    yield
    crypto._key = None


def test_migration_0019_drops_structured(tmp_path):
    url = _sqlite_url(tmp_path)
    command.upgrade(_cfg(url), "0018")
    engine = create_engine(url)
    try:
        assert "structured" in _columns(engine, "analysis_result")
    finally:
        engine.dispose()

    command.upgrade(_cfg(url), "0019")
    engine = create_engine(url)
    try:
        cols = _columns(engine, "analysis_result")
        assert "structured" not in cols
        # 速览字段必须保留。
        assert {"summary", "tags", "model", "routing"} <= cols
    finally:
        engine.dispose()
