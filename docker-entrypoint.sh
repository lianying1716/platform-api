#!/bin/sh
# 生产启动入口：先执行 Alembic 迁移（幂等，重复运行安全），再 exec 真正的 CMD。
# compose 里 PLATFORM_AUTO_CREATE_TABLES=false，建表完全由 Alembic 负责。
#
# 契约 I2（迁移单飞 + 互斥）：
# - PLATFORM_RUN_MIGRATIONS=false 时本容器不迁移（由同栈中负责迁移的容器先做，
#   worker 即如此；它靠 depends_on: service_healthy 等 api 迁移完再起）。
# - 即使配置失误导致多容器同时迁移，alembic/env.py 的 advisory lock 也会串行化，
#   不会并发改库。两层保护，任一层生效即可保证安全。
set -e

if [ "${PLATFORM_RUN_MIGRATIONS:-true}" = "false" ]; then
  echo "[entrypoint] PLATFORM_RUN_MIGRATIONS=false，跳过迁移（由负责迁移的容器执行）"
else
  echo "[entrypoint] 执行数据库迁移：alembic upgrade head"
  if ! alembic upgrade head; then
    echo "[entrypoint] 迁移失败：拒绝启动，保持数据库在迁移前状态（单事务回滚）。" >&2
    echo "[entrypoint] 请查看上方 Alembic 报错；如需回滚请用 scripts/restore.sh 恢复备份。" >&2
    exit 1
  fi
fi

exec "$@"
