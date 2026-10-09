from collections.abc import Generator

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
    from app import models  # noqa: F401  确保模型注册

    Base.metadata.create_all(engine)
