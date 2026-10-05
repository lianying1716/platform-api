#!/bin/sh
# 生产启动入口：先执行 Alembic 迁移（幂等，重复运行安全），再 exec 真正的 CMD。
# compose 里 PLATFORM_AUTO_CREATE_TABLES=false，建表完全由 Alembic 负责。
set -e

alembic upgrade head

exec "$@"
