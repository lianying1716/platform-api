#!/usr/bin/env bash
# 从备份还原（契约 I4）。回到升级前的可运行状态。
#
# 用法：./scripts/restore.sh backups/backup-v1.2.0-20261011T....tar.gz
#
# 步骤：停服务 → 还原 DB（pg_restore）→ 还原 /data 卷 → 起服务 → 校验版本端点。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PKG="${1:-}"
[ -n "$PKG" ] && [ -f "$PKG" ] || { echo "用法: $0 <backup-*.tar.gz>" >&2; exit 1; }

DATA_VOLUME="${DATA_VOLUME:-$(basename "$ROOT")_platform-data}"

echo "==> 校验备份完整性"
if [ -f "$PKG.sha256" ]; then
  sha256sum -c "$PKG.sha256"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar xzf "$PKG" -C "$WORK"
[ -s "$WORK/db.dump" ] || { echo "!! 备份缺 db.dump" >&2; exit 1; }

# 关键：data.tar.gz 必须在动 /data 之前就校验存在且可解压。
# 旧版只校验 db.dump，却在清空 /data 后才尝试解压 data.tar.gz——
# 一旦备份缺这个文件，secret.key 就被永久抹掉（= 所有凭证作废）。
[ -s "$WORK/data.tar.gz" ] || { echo "!! 备份缺 data.tar.gz（含 secret.key），拒绝还原" >&2; exit 1; }
if ! tar tzf "$WORK/data.tar.gz" >/dev/null 2>&1; then
  echo "!! data.tar.gz 损坏（无法解压），拒绝还原" >&2; exit 1
fi

echo "==> [1/4] 停止 api/worker（保留 db）"
docker compose stop platform-api platform-worker

echo "==> [2/4] 还原数据库（--clean 覆盖）"
docker compose exec -T db pg_restore -U "${POSTGRES_USER:-platform}" -d "${POSTGRES_DB:-platform}" \
  --clean --if-exists < "$WORK/db.dump"

# 先解压到卷内临时目录并确认成功，再原子替换原内容——避免"先清空、后失败"丢数据。
echo "==> [3/4] 还原 /data 卷"
docker run --rm -v "$DATA_VOLUME":/data -v "$WORK":/backup alpine sh -c '
  set -e
  rm -rf /data/.restore-stage /data/.restore-old
  mkdir -p /data/.restore-stage
  tar xzf /backup/data.tar.gz -C /data/.restore-stage
  # 解压成功后再做替换：旧内容先挪走，新内容就位，最后删旧。
  mkdir -p /data/.restore-old
  find /data -mindepth 1 -maxdepth 1 ! -name ".restore-stage" ! -name ".restore-old" \
    -exec mv {} /data/.restore-old/ \;
  find /data/.restore-stage -mindepth 1 -maxdepth 1 -exec mv {} /data/ \;
  rm -rf /data/.restore-stage /data/.restore-old
'

echo "==> [4/4] 重启"
docker compose up -d

echo "==> 完成。校验：curl -s localhost:9100/api/system/version"
