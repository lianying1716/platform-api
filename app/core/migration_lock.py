"""迁移互斥锁（契约 I2）。

多个进程（api / worker / 多副本）同时执行 `alembic upgrade head` 会各自读到相同的
当前 revision 并竞争，Grafana 曾因此出现"迁移失败 → 容器 boot loop"。
这里用 PostgreSQL 会话级 advisory lock 把整批迁移串行化：同一时刻只有一个进程真正迁移，
其余等待；拿不到锁超时则失败退出（可重试），绝不并发改库。

非 PostgreSQL（本地/测试 SQLite）自动跳过——SQLite 本就是单写者，且不支持该锁。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

logger = logging.getLogger("platform.migration_lock")

# 固定 key：同一平台的所有迁移实例必须用同一个值才能互斥。
LOCK_KEY = 0x706C6174666D676B  # "platform" 的十六进制变形，仅需全局唯一


def is_postgres(connection) -> bool:
    return connection.dialect.name == "postgresql"


@contextmanager
def migration_lock(connection, timeout_seconds: int = 60):
    """在支持的数据库上持锁执行迁移；SQLite 等无锁方言直接放行。

    锁是会话级的：在整个 with 块内有效，退出时释放（连接关闭也会自动释放）。
    """
    if not is_postgres(connection):
        yield
        return

    from sqlalchemy import text

    # lock_timeout 对 advisory lock 同样生效：等不到锁就抛错，而不是无限挂起。
    # 记下原值，退出时复位，避免污染复用连接上的后续语句。
    previous = connection.execute(text("SHOW lock_timeout")).scalar() or "0"
    connection.execute(text(f"SET lock_timeout = '{int(timeout_seconds)}s'"))
    logger.info("等待迁移锁（key=0x%x，超时 %ss）", LOCK_KEY, timeout_seconds)
    try:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": LOCK_KEY})
    except Exception as exc:  # noqa: BLE001 拿不到锁属"可重试"失败，回滚干净后抛出
        connection.rollback()
        raise RuntimeError(
            f"等待迁移锁超时（{timeout_seconds}s），另一进程可能正在迁移；稍后重试"
        ) from exc
    try:
        yield
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
        connection.execute(text(f"SET lock_timeout = '{previous}'"))
