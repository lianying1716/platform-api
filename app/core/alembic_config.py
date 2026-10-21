"""Build Alembic configs without configparser interpolating URL escapes.

Alembic stores options in a configparser subclass.  Assigning a SQLAlchemy URL
such as ``postgresql+psycopg2://alice:p%40ss@db/archive`` makes configparser
treat ``%40`` as interpolation syntax and raise before a connection is even
attempted.  Keep the URL in the Alembic attributes object (the same handoff
used by Alembic's own programmatic API) and leave only non-secret paths in the
ini file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config


def make_config(root: Path, db_url: str) -> Config:
    project_root = Path(root).resolve()
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.attributes["sqlalchemy_url"] = db_url
    # Clear the template placeholder.  The URL is read from attributes by
    # alembic/env.py; this also prevents later code from seeing ``driver://``.
    cfg.set_main_option("sqlalchemy.url", "")
    return cfg


def configured_url(cfg: Config) -> str:
    value = str(getattr(cfg, "attributes", {}).get("sqlalchemy_url") or "").strip()
    if value:
        return value
    # Backward compatibility for tests and integrations that still use
    # ``Config.set_main_option("sqlalchemy.url", ...)``.  Do not call this for
    # percent-encoded passwords; ``make_config`` stores those in attributes.
    try:
        value = (cfg.get_main_option("sqlalchemy.url") or "").strip()
    except ValueError:  # configparser interpolation or malformed option
        return ""
    if value.startswith("driver://"):
        return ""
    return value
