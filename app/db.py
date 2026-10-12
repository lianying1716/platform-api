from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.resolved_database_url)


def enable_sqlite_foreign_keys(target_engine) -> None:
    """SQLite 默认不启用外键；挂 connect 钩子，让本地/测试与 PG 行为一致。"""
    if target_engine.dialect.name != "sqlite":
        return

    @event.listens_for(target_engine, "connect")
    def _enable(dbapi_connection, _record):  # pragma: no cover - 连接钩子
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


enable_sqlite_foreign_keys(engine)


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # 生产由 Alembic 负责建表（见 docker-entrypoint.sh）；关闭自动建表时直接跳过。
    if not settings.auto_create_tables:
        return

    # 若库已由 Alembic 管理（存在 alembic_version 且有记录），create_all 会与迁移链竞争：
    # 它既不写 revision、也不会加列，之后 `alembic upgrade` 会从 0001 重跑撞表。
    # 这种库完全交给 Alembic，init_db 不插手。
    from sqlalchemy import inspect

    if inspect(engine).has_table("alembic_version"):
        with engine.connect() as conn:
            from sqlalchemy import text

            if conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar():
                return

    from app import models  # noqa: F401  确保模型注册

    Base.metadata.create_all(engine)

    # create_all 建的是"当前模型"结构，正好对应迁移链 head。这里把版本戳到 head，
    # 使之后的 `alembic upgrade head` 成为空操作，不再撞表（修复审查发现的冲突）。
    from app.core.version import db_head

    head = db_head()
    if head:
        from alembic import command
        from alembic.config import Config

        root = Path(__file__).resolve().parent.parent
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        cfg.set_main_option("sqlalchemy.url", settings.resolved_database_url)
        command.stamp(cfg, head)
