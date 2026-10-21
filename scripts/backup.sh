#!/usr/bin/env bash
# 升级前强制备份（契约 I4）：数据库 + /data 卷（含 secret.key）+ 版本信息。
#
# 用法：
#   ./scripts/backup.sh                     # Docker Compose 部署
#   BACKUP_DIR=/path ./scripts/backup.sh    # 指定输出目录
#
# 产物（默认 ./backups/）：
#   backup-<version>-<ts>.tar.gz   含 db.dump + data.tar + manifest.json
#   backup-<version>-<ts>.sha256   校验和
#
# 红线：secret.key 丢失 = 所有凭证作废，因此 /data 必须进备份。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

# shellcheck disable=SC1091
. "$ROOT/scripts/lib/compose.sh"

# 版本号：显式 env > compose 解析出的 pin > git tag > dev。
VERSION="${PLATFORM_VERSION:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(compose_app_version)"
fi
if [ -z "$VERSION" ]; then
  VERSION="$(git describe --tags --always 2>/dev/null || echo dev)"
fi
DB_USER="$(compose_service_env db POSTGRES_USER platform)"
DB_NAME="$(compose_service_env db POSTGRES_DB platform)"

mkdir -p "$BACKUP_DIR"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 卷名必须来自 compose 解析结果，不能按部署目录名猜。
DATA_VOLUME="${DATA_VOLUME:-$(compose_volume_name platform-data)}"
if [ -z "$DATA_VOLUME" ] || ! docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1; then
  echo "!! 找不到 compose 的 data 卷（可用 DATA_VOLUME=xxx 指定）。" >&2
  echo "   请确认当前目录是实际部署目录，且 .env 中的 COMPOSE_PROJECT_NAME 与运行时一致。" >&2
  echo "   查询：docker volume ls | grep platform-data" >&2
  exit 1
fi

echo "==> 备份版本 $VERSION → $BACKUP_DIR"

# ---- 1) 数据库 ----
echo "==> [1/3] pg_dump"
if docker compose ps db >/dev/null 2>&1 && [ -n "$(docker compose ps -q db 2>/dev/null)" ]; then
  docker compose exec -T db pg_dump -U "$DB_USER" \
    -d "$DB_NAME" -Fc > "$WORK/db.dump"
else
  echo "!! 未找到运行中的 compose db 容器；请用 DB 相关环境变量或手动备份后重试。" >&2
  exit 1
fi
[ -s "$WORK/db.dump" ] || { echo "!! pg_dump 产物为空，备份失败" >&2; exit 1; }

# ---- 2) data 卷（含 secret.key）----
echo "==> [2/3] /data 卷"
docker run --rm -v "$DATA_VOLUME":/data -v "$WORK":/backup alpine \
  tar czf /backup/data.tar.gz -C /data . 2>/dev/null \
  || { echo "!! /data 卷备份失败" >&2; exit 1; }

# ---- 3) manifest ----
echo "==> [3/3] manifest"
DB_REV="$(docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
  -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '[:space:]' || echo unknown)"
cat > "$WORK/manifest.json" <<JSON
{
  "version": "$VERSION",
  "created_at": "$TS",
  "db_revision": "$DB_REV",
  "includes": ["db.dump", "data.tar.gz"],
  "note": "data.tar.gz 含 secret.key 与本地文件；恢复时必须一并还原，否则凭证作废。"
}
JSON

PKG="$BACKUP_DIR/backup-$VERSION-$TS.tar.gz"
tar czf "$PKG" -C "$WORK" .
sha256sum "$PKG" > "$PKG.sha256"
echo "==> 完成：$PKG"
echo "==> 校验：$PKG.sha256"
