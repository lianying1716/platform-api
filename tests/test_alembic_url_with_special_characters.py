"""Alembic must not pass a URL through configparser interpolation.

The database password is URL-encoded by SQLAlchemy.  If that encoded string is
written with ``Config.set_main_option``, configparser interprets ``%40`` as an
interpolation token and migration fails before connecting.  This is a very
common password shape (``p@ss:word/``), so keep the URL in Alembic attributes
and verify an actual migration runs against PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.alembic_config import configured_url, make_config


ROOT = Path(__file__).resolve().parent.parent


def test_make_config_keeps_percent_encoded_password_out_of_configparser():
    url = "postgresql+psycopg2://alice:p%40ss%3Aword%2F@db:5432/archive"
    cfg = make_config(ROOT, url)
    assert configured_url(cfg) == url
    assert cfg.get_main_option("sqlalchemy.url") == ""


def test_migration_accepts_percent_encoded_password_on_real_postgres(tmp_path, monkeypatch):
    """Opt-in integration test; never touches a database without XSAI_TEST_PG_URL."""
    url = os.environ.get("XSAI_TEST_PG_URL", "").strip()
    if not url:
        pytest.skip("set XSAI_TEST_PG_URL to run the PostgreSQL special-password migration")
    parsed = make_url(url)
    # ``make_url`` decodes percent escapes; the point is the decoded secret
    # must survive intact, not that the caller passed an encoded string.
    assert parsed.password == "p@ss:word/"
    key = tmp_path / "secret.key"
    key.write_text("test-key-not-used-for-credentials", encoding="utf-8")
    monkeypatch.setenv("PLATFORM_DATABASE_URL", url)
    monkeypatch.setenv("PLATFORM_CREDENTIAL_KEY_FILE", str(key))

    # The caller must supply a disposable database. Reusing the URL exactly is
    # the point: the encoded password must survive the full Alembic handoff.
    command.upgrade(make_config(ROOT, url), "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert revision
    engine.dispose()
