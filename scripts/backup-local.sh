#!/usr/bin/env bash
# Local (no-Docker) backup for source installs.
#
# SQLite + files live under PLATFORM_DATA_DIR.  PostgreSQL installs also get a
# pg_dump when pg_dump is available; without it we refuse to pretend the backup
# is complete.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOCAL_ENV="${PLATFORM_LOCAL_ENV_FILE:-$ROOT/.env.local-install}"
if [ -f "$LOCAL_ENV" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$LOCAL_ENV"
  set +a
fi
: "${PLATFORM_DATA_DIR:=$ROOT/data}"
: "${PLATFORM_DATABASE_URL:=}"

DATA_DIR="$PLATFORM_DATA_DIR"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
VERSION="${PLATFORM_VERSION:-$(git describe --tags --always 2>/dev/null || echo dev)}"
mkdir -p "$BACKUP_DIR"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[ -d "$DATA_DIR" ] || { echo "!! 数据目录不存在：$DATA_DIR" >&2; exit 1; }
tar czf "$WORK/data.tar.gz" -C "$DATA_DIR" .

DB_URL="${PLATFORM_DATABASE_URL:-}"
if [ -n "$DB_URL" ] && [[ "$DB_URL" == postgresql* ]]; then
  command -v pg_dump >/dev/null 2>&1 || {
    echo "!! PostgreSQL 部署缺少 pg_dump，无法生成完整备份；请安装 PostgreSQL 客户端后重试" >&2
    exit 1
  }
  pg_dump --dbname="$DB_URL" --format=custom --file="$WORK/db.dump"
elif [ -f "$DATA_DIR/platform.db" ]; then
  cp "$DATA_DIR/platform.db" "$WORK/platform.db"
else
  echo "!! 找不到 SQLite platform.db 且未配置 PostgreSQL；拒绝生成不可恢复的备份" >&2
  exit 1
fi

if [ -f "$WORK/db.dump" ]; then
  DB_KIND="postgresql"
  DB_FILE="db.dump"
else
  DB_KIND="sqlite"
  DB_FILE="platform.db"
fi
python3 - "$WORK/manifest.json" "$VERSION" "$TS" "$DB_KIND" "$DB_FILE" <<'PY'
import json, sys
path, version, created_at, kind, db_file = sys.argv[1:]
with open(path, "w", encoding="utf-8") as fh:
    json.dump({
        "version": version,
        "created_at": created_at,
        "database": kind,
        "includes": ["data.tar.gz", db_file],
    }, fh, ensure_ascii=False)
PY

PKG="$BACKUP_DIR/backup-$VERSION-$TS.tar.gz"
tar czf "$PKG" -C "$WORK" .
sha256sum "$PKG" > "$PKG.sha256"
echo "==> 完成：$PKG"
echo "==> 校验：$PKG.sha256"
