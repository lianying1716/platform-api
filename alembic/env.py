import sys
from logging.config import fileConfig
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alembic import context  # noqa: E402
from sqlalchemy import engine_from_config, pool  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.alembic_config import configured_url  # noqa: E402
import app.models  # noqa: F401,E402  注册全部模型
from app.db import Base  # noqa: E402

config = context.config
# 显式传入的 sqlalchemy.url（如迁移集成测试的临时库）优先；否则用项目配置。
# 注意：alembic.ini 里默认是占位符 `driver://user:pass@localhost/dbname`，
# 它非空但不可用。若直接采用，`alembic upgrade head` 会报 `Can't load plugin:
# sqlalchemy.dialects:driver`——即命令行迁移永远跑不起来。这里把占位符视为“未设置”。
_url = configured_url(config)
if not _url:
    _url = settings.resolved_database_url
config.attributes["sqlalchemy_url"] = _url
target_metadata = Base.metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    context.configure(
        url=configured_url(config),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from app.core.migration_lock import migration_lock

    from sqlalchemy import create_engine

    connectable = create_engine(configured_url(config), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # 契约 I2：用 DB 级 advisory lock 串行化迁移，避免多进程并发迁移互相打架。
        # 非 PG（SQLite）自动跳过，行为不变。
        with migration_lock(connection):
            # advisory lock 必须在事务外获取/释放（会话级），故此处单独提交一次。
            connection.commit()
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
