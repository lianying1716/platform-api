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

# 版本号：优先镜像 env，其次 git tag，最后 dev。
VERSION="${PLATFORM_VERSION:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(git describe --tags --always 2>/dev/null || echo dev)"
fi

mkdir -p "$BACKUP_DIR"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# data 卷名默认按 compose 项目名推导（= 目录名），可用 DATA_VOLUME 覆盖。
DATA_VOLUME="${DATA_VOLUME:-$(basename "$ROOT")_platform-data}"
if ! docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1; then
  echo "!! 找不到数据卷 ${DATA_VOLUME}（可用 DATA_VOLUME=xxx 指定）。" >&2
  echo "   查询：docker volume ls | grep platform" >&2
  exit 1
fi

echo "==> 备份版本 $VERSION → $BACKUP_DIR"

# ---- 1) 数据库 ----
echo "==> [1/3] pg_dump"
if docker compose ps db >/dev/null 2>&1 && [ -n "$(docker compose ps -q db 2>/dev/null)" ]; then
  docker compose exec -T db pg_dump -U "${POSTGRES_USER:-platform}" \
    -d "${POSTGRES_DB:-platform}" -Fc > "$WORK/db.dump"
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
DB_REV="$(docker compose exec -T db psql -U "${POSTGRES_USER:-platform}" -d "${POSTGRES_DB:-platform}" \
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
