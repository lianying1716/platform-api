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

echo "==> 升级目标：${TARGET}（当前 $(git describe --tags --always 2>/dev/null || echo unknown)）"

MODE="${MODE:-docker}"
if [ "$MODE" = "local" ]; then
  # shellcheck disable=SC1091
  . "$ROOT/scripts/lib/local-install.sh"
  local_load_env
fi

# ---- 强制备份：没有成功备份就不进入升级 ----
echo "==> [0/3] 强制备份"
# 原实现只给子进程临时 BUILD_VERSION，backup.sh 读的却是 PLATFORM_VERSION，
# 结果备份包永远显示 dev。两边统一用 PLATFORM_VERSION。
if [ "$MODE" = "local" ]; then
  PLATFORM_VERSION="$TARGET" ./scripts/backup-local.sh
else
  PLATFORM_VERSION="$TARGET" ./scripts/backup.sh
fi

if [ "$MODE" = "local" ]; then
  # Stop before checkout: a running worker must not execute mixed old Python
  # modules from a worktree whose files are being replaced tag by tag.
  local_stop
  echo "==> [1/3] 本地源码：fetch tag + checkout"
  git fetch --tags
  # A deployment checkout must never trigger an implicit merge or branch
  # switch while the operator thinks they are upgrading to a release tag.
  git check-ref-format "refs/tags/$TARGET" >/dev/null 2>&1 \
    || { echo "!! 目标必须是发布 tag（例如 v1.0.9），不能是任意 ref" >&2; exit 1; }
  git rev-parse --verify --quiet "refs/tags/$TARGET^{commit}" >/dev/null \
    || { echo "!! 找不到 tag $TARGET；请确认版本号或先 git fetch --tags" >&2; exit 1; }
  git checkout --detach "refs/tags/$TARGET"
  echo "==> [2/3] 同步依赖"
  uv sync --extra postgres --extra browser --extra transcribe

  # Source upgrades must replace the browser bundle as well as Python.  The
  # service is still stopped here, so a build failure leaves the old API+worker
  # untouched and the previous app/static intact.  SKIP_WEB_BUILD is only for a
  # backend-only install that explicitly has no served frontend.
  if [ "${SKIP_WEB_BUILD:-0}" != "1" ]; then
    command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 || {
      echo "!! 源码升级缺少 Node/npm，无法重建前端；为免旧页面配新后端，已中止" >&2
      echo "   安装 Node 22+ 后重试；仅部署 API 时可用 SKIP_WEB_BUILD=1 并设置 PLATFORM_SERVE_FRONTEND=false" >&2
      exit 1
    }
    echo "==> [2/3] 构建前端正式包（成功后才替换 app/static）"
    STATIC_STAGE="$(mktemp -d "$ROOT/.app-static-XXXXXX")"
    STATIC_BACKUP="$ROOT/.app-static-backup-$$"
    cleanup_static() { rm -rf "$STATIC_STAGE"; }
    trap cleanup_static EXIT
    (
      cd web
      npm ci --no-audit --no-fund >/dev/null 2>&1 || npm install --no-audit --no-fund >/dev/null 2>&1
      npx vite build --outDir "$STATIC_STAGE" --emptyOutDir
    )
    test -f "$STATIC_STAGE/index.html" || {
      echo "!! 前端构建未产出 index.html；保留旧 app/static 并中止" >&2
      exit 1
    }
    # An EXIT trap runs after a successful `exit 0` too, so cleanup must be
    # explicit here. Otherwise the freshly swapped app/static would be removed.
    if [ -e app/static ]; then mv app/static "$STATIC_BACKUP"; fi
    if ! mv "$STATIC_STAGE" app/static; then
      [ ! -e "$STATIC_BACKUP" ] || mv "$STATIC_BACKUP" app/static
      exit 1
    fi
    rm -rf "$STATIC_BACKUP"
    STATIC_BACKUP=""
    trap - EXIT
    unset STATIC_STAGE
  fi

  echo "==> [3/3] 迁移（单事务 + 互斥锁）"
  uv run alembic upgrade head
  if systemctl --user cat stargather-platform-api.service >/dev/null 2>&1; then
    systemctl --user restart stargather-platform-api stargather-platform-worker
    echo "==> 已重启 systemd 用户服务（API + worker）"
  elif [ "$(uname -s)" = "Darwin" ] && launchctl list 2>/dev/null | grep -q com.stargather.platform-api; then
    launchctl kickstart -k "gui/$(id -u)/com.stargather.platform-api"
    launchctl kickstart -k "gui/$(id -u)/com.stargather.platform-worker"
    echo "==> 已重启 launchd 服务（API + worker）"
  else
    local_start
    echo "==> 已重启后台 API + worker（日志在 ${PLATFORM_DATA_DIR:-$ROOT/data}/server.log 与 worker.log）"
  fi
  PORT="${PLATFORM_PORT:-9100}"
  for _ in $(seq 1 45); do
    if curl -fsS "http://127.0.0.1:${PORT}/api/system/version" >/dev/null 2>&1; then
      echo "==> 服务已就绪："
      curl -s "http://127.0.0.1:${PORT}/api/system/version"; echo
      exit 0
    fi
    sleep 2
  done
  echo "!! 升级完成但 90 秒内未就绪；先检查日志，回滚用 ./scripts/restore-local.sh <备份包>" >&2
  echo "   备份目录：${BACKUP_DIR:-$ROOT/backups}" >&2
  exit 1
else
  echo "==> [1/3] 拉取镜像 $TARGET"
  PLATFORM_VERSION="$TARGET" docker compose pull
  echo "==> [2/3] 启动（api 先迁移，worker 等 api 健康）"
  PLATFORM_VERSION="$TARGET" docker compose up -d
  echo "==> [3/3] 等待健康检查"
  # 读 compose 解析出的宿主端口；健康探针不能写死，否则改端口后升级永远误报失败。
  # shellcheck disable=SC1091
  . "$ROOT/scripts/lib/compose.sh"
  PORT="$(compose_host_port)"
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${PORT}/api/system/version" >/dev/null 2>&1; then
      echo "==> 服务已就绪："
      curl -s "http://127.0.0.1:${PORT}/api/system/version"; echo
      exit 0
    fi
    sleep 3
  done
  echo "!! 90 秒内未就绪；请查看 docker compose logs。回滚：./scripts/restore.sh <刚才的备份>" >&2
  exit 1
fi
