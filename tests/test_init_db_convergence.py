"""init_db 与 Alembic 的收敛：create_all 库不得与迁移链打架。

回归背景（对抗性审查发现）：create_all 建表却不写 alembic_version，
之后 `alembic upgrade head` 会从 0001 重跑并撞 "table already exists"。
修复后：create_all 建表即 stamp 到 head；已有 alembic 记录的库跳过 create_all。

注意：本测试用 monkeypatch 替换 app.db.engine（而非 importlib.reload），
reload 会永久替换模块级 engine 并污染后续所有测试。
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _cfg(db_url: str) -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_create_all_then_alembic_upgrade_is_noop(tmp_path, monkeypatch):
    """create_all（经 init_db）后跑 alembic upgrade head，不应报错。"""
    url = "sqlite:///" + str(tmp_path / "conv.db")
    engine = create_engine(url)

    import app.config as cfg_mod
    import app.db

    monkeypatch.setattr(app.db, "engine", engine)
    monkeypatch.setattr(cfg_mod.settings, "auto_create_tables", True, raising=False)
    monkeypatch.setattr(cfg_mod.settings, "database_url", url, raising=False)
    monkeypatch.setenv("PLATFORM_CREDENTIAL_ENC_KEY", "x" * 4)

    app.db.init_db()

    # 关键断言：init_db 后库已有 alembic 版本记录（= head），因此 upgrade 是空操作。
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert rev, "create_all 后必须 stamp 到某个 head，否则后续 upgrade 会撞表"

    command.upgrade(_cfg(url), "head")  # 不应抛异常
    with engine.connect() as conn:
        rev2 = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert rev2 == rev
    engine.dispose()


def test_init_db_skips_create_all_when_alembic_managed(tmp_path, monkeypatch):
    """已有 alembic 记录的库：init_db 不得再 create_all（避免与迁移链竞争）。"""
    url = "sqlite:///" + str(tmp_path / "managed.db")
    command.upgrade(_cfg(url), "head")  # 由 Alembic 建库
    engine = create_engine(url)

    import app.config as cfg_mod
    import app.db

    monkeypatch.setattr(app.db, "engine", engine)
    monkeypatch.setattr(cfg_mod.settings, "auto_create_tables", True, raising=False)

    before = set(inspect(engine).get_table_names())
    app.db.init_db()
    after = set(inspect(engine).get_table_names())
    assert before == after, "Alembic 管理的库不应被 create_all 改动"
    engine.dispose()
