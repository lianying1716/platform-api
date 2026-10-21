#!/usr/bin/env bash
# Restore a no-Docker source install from scripts/backup-local.sh.
#
# Usage: ./scripts/restore-local.sh backups/backup-<version>-<timestamp>.tar.gz
#
# The archive is verified before the live data directory is touched.  API and
# worker are stopped by their recorded PIDs (never by port), the SQLite or
# PostgreSQL database is restored, then both processes are started again.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PKG="${1:-}"
[ -n "$PKG" ] && [ -f "$PKG" ] || { echo "用法: $0 <backup-*.tar.gz>" >&2; exit 1; }

# shellcheck disable=SC1091
. "$ROOT/scripts/lib/local-install.sh"
local_load_env

DATA_DIR="$(local_data_dir)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
PORT="${PLATFORM_PORT:-9100}"
ENV_FILE="$(local_env_file)"

# Validate everything that can be validated before touching live processes.
# A restore that stops the worker and then discovers a missing config has
# already caused an outage and must never reach that state.
[ -f "$ENV_FILE" ] || { echo "!! 缺少安装配置 $ENV_FILE；请先运行 scripts/install-local.sh" >&2; exit 1; }

if [ -f "$PKG.sha256" ]; then
  (cd "$(dirname "$PKG")" && sha256sum -c "$(basename "$PKG.sha256")")
fi

WORK="$(mktemp -d)"
SAFETY=""
cleanup() {
  [ -z "${WORK:-}" ] || rm -rf "$WORK"
  if [ -n "$SAFETY" ]; then
    rm -rf "$SAFETY"
  fi
  return 0
}
trap cleanup EXIT
tar xzf "$PKG" -C "$WORK"

[ -s "$WORK/manifest.json" ] || { echo "!! 备份缺 manifest.json，拒绝恢复" >&2; exit 1; }
[ -s "$WORK/data.tar.gz" ] || { echo "!! 备份缺 data.tar.gz（含 secret.key），拒绝恢复" >&2; exit 1; }
tar tzf "$WORK/data.tar.gz" >/dev/null 2>&1 || { echo "!! data.tar.gz 损坏，拒绝恢复" >&2; exit 1; }

DB_KIND="$(python3 - "$WORK/manifest.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    print(json.load(fh).get("database", ""))
PY
)"
case "$DB_KIND" in
  sqlite) [ -s "$WORK/platform.db" ] || { echo "!! 备份声明为 SQLite 但缺 platform.db" >&2; exit 1; } ;;
  postgresql) [ -s "$WORK/db.dump" ] || { echo "!! 备份声明为 PostgreSQL 但缺 db.dump" >&2; exit 1; } ;;
  *) echo "!! manifest 的 database 字段无效：${DB_KIND:-空}" >&2; exit 1 ;;
esac

DB_URL="${PLATFORM_DATABASE_URL:-}"
if [ "$DB_KIND" = "postgresql" ]; then
  case "$DB_URL" in
    postgresql*) ;;
    *) echo "!! 当前配置未指向 PostgreSQL，但备份是 PostgreSQL；拒绝混用" >&2; exit 1 ;;
  esac
  command -v pg_restore >/dev/null 2>&1 || { echo "!! 缺少 pg_restore，无法恢复 PostgreSQL 备份" >&2; exit 1; }
elif [ -n "$DB_URL" ] && [[ "$DB_URL" != sqlite:* ]]; then
  echo "!! 当前配置是 PostgreSQL，但备份是 SQLite；拒绝混用" >&2
  exit 1
fi

echo "==> [1/4] 停止 API 与 worker"
local_stop_services

# Keep the live database recoverable if a later filesystem step fails.
if [ "$DB_KIND" = "sqlite" ] && [ -f "$DATA_DIR/platform.db" ]; then
  mkdir -p "$BACKUP_DIR"
  prior_db="$BACKUP_DIR/pre-restore-$TS.db"
  cp "$DATA_DIR/platform.db" "$prior_db"
  echo "    已保留恢复前数据库：$prior_db"
fi

echo "==> [2/4] 恢复数据库"
mkdir -p "$DATA_DIR"
if [ "$DB_KIND" = "postgresql" ]; then
  # PostgreSQL lives outside the data directory, so it must be restored
  # explicitly before the filesystem swap. SQLite is restored as part of the
  # staged data directory below.
  pg_restore --dbname="$DB_URL" --clean --if-exists --no-owner --no-privileges < "$WORK/db.dump"
fi

echo "==> [3/4] 恢复数据目录（原子替换）"
STAGE="$DATA_DIR.restore-stage-$$"
OLD="$DATA_DIR.restore-old-$$"
SAFETY="$OLD"
rm -rf "$STAGE"
mkdir -p "$STAGE"
tar xzf "$WORK/data.tar.gz" -C "$STAGE"
# SQLite 的权威库在 data.tar.gz 里；用备份包内的副本覆盖解压结果，确保
# 备份时单独校验过的数据库就是恢复后实际运行的库。
[ "$DB_KIND" != "sqlite" ] || cp "$WORK/platform.db" "$STAGE/platform.db"
if [ -e "$DATA_DIR" ]; then mv "$DATA_DIR" "$OLD"; fi
mv "$STAGE" "$DATA_DIR"
rm -rf "$OLD"
SAFETY=""

echo "==> [4/4] 启动 API 与 worker"
local_start_services

echo "==> 等待就绪：http://127.0.0.1:${PORT}/api/system/version"
READY=0
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/system/version"; then
    echo
    READY=1
    break
  fi
  sleep 2
done
if [ "$READY" != "1" ]; then
  echo "!! 已恢复数据但服务 90 秒内未就绪；查看 $DATA_DIR/server.log 与 $DATA_DIR/worker.log" >&2
  exit 1
fi
