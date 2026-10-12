#!/usr/bin/env bash
# 升级编排（契约 I4 前置：先备份，失败即中止）。
#
# 用法：
#   ./scripts/upgrade.sh v1.3.0          # Docker Compose 部署
#   MODE=local ./scripts/upgrade.sh v1.3.0
#
# 设计：不做 Alembic downgrade——结构变更不可逆，回滚一律用 scripts/restore.sh。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TARGET="${1:-}"
[ -n "$TARGET" ] || { echo "用法: $0 <目标版本如 v1.3.0>" >&2; exit 1; }

echo "==> 升级目标：$TARGET（当前 $(git describe --tags --always 2>/dev/null || echo unknown)）"

# ---- 强制备份：没有成功备份就不进入升级 ----
echo "==> [0/3] 强制备份"
BUILD_VERSION="$TARGET" ./scripts/backup.sh

MODE="${MODE:-docker}"
if [ "$MODE" = "local" ]; then
  echo "==> [1/3] 本地源码：fetch tag + checkout"
  git fetch --tags
  git checkout "$TARGET"
  echo "==> [2/3] 同步依赖"
  uv sync --extra postgres --extra browser
  echo "==> [3/3] 迁移（单事务 + 互斥锁）"
  uv run alembic upgrade head
  echo "==> 完成。若用 systemd 请重启：systemctl --user restart platform-api-dev platform-web-dev"
else
  echo "==> [1/3] 拉取镜像 $TARGET"
  PLATFORM_VERSION="$TARGET" docker compose pull
  echo "==> [2/3] 启动（api 先迁移，worker 等 api 健康）"
  PLATFORM_VERSION="$TARGET" docker compose up -d
  echo "==> [3/3] 等待健康检查"
  for _ in $(seq 1 30); do
    if curl -fsS localhost:9100/api/system/version >/dev/null 2>&1; then
      echo "==> 服务已就绪："
      curl -s localhost:9100/api/system/version; echo
      exit 0
    fi
    sleep 3
  done
  echo "!! 90 秒内未就绪；请查看 docker compose logs。回滚：./scripts/restore.sh <刚才的备份>" >&2
  exit 1
fi
