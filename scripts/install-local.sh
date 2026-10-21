#!/usr/bin/env bash
# 无 Docker 环境的「一键安装」（macOS / Linux 裸机）。
#
# 背景：仓库默认部署方式是 Docker Compose，但要用户先在 macOS 上装 Docker Desktop
# 摩擦很大；真实反馈里用户直接在 Mac 上让 agent「把项目跑起来」，agent 只能临场发挥、
# 每次步骤都不一样。本脚本把那条路固定下来，**不需要 Docker、不需要 PostgreSQL**：
# 用 uv 装依赖、SQLite 存数据、构建前端正式包，然后以生产模式起服务。
#
# 用法：
#   ./scripts/install-local.sh              # 装好并启动（SQLite + 托管前端正式包）
#   ./scripts/install-local.sh --no-start   # 只装不启动
#   ./scripts/install-local.sh --postgres 'postgresql+psycopg2://user:pw@host:5432/db'
#   ./scripts/install-local.sh --port 9100 --host 0.0.0.0
#   ./scripts/install-local.sh --service     # 装好并注册为常驻服务（开机自启/崩溃自拉起）
#
# 环境变量（可选）：
#   PLATFORM_DATA_DIR     数据目录（默认 ./data，存 SQLite/密钥/文件）
#   PLATFORM_PORT         端口（默认 9100）
#   PLATFORM_HOST         绑定地址（默认 127.0.0.1；局域网访问用 0.0.0.0）
#   PLATFORM_LOCAL_ENV_FILE  安装配置输出路径（默认 ./.env.local-install）
#   SKIP_BROWSER=1        不装 Playwright（跳过抖音/小红书扫码能力）
#   SKIP_TRANSCRIBE=1     不装 faster-whisper（跳过本地语音转录）
#   SKIP_WEB_BUILD=1      不构建前端（app/static 已有产物时用）
#
# 前置：uv（Python 包管理）、ffmpeg（下载转码与转录）。Node 仅在前端构建时需要。
#
# 幂等：可重复运行；已存在的数据与密钥不会被覆盖。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HOST="${PLATFORM_HOST:-127.0.0.1}"
PORT="${PLATFORM_PORT:-9100}"
DATA_DIR="${PLATFORM_DATA_DIR:-$ROOT/data}"
NO_START=0
AS_SERVICE=0
DB_URL=""

while [ $# -gt 0 ]; do
  case "$1" in
    --no-start) NO_START=1; shift ;;
    --service) AS_SERVICE=1; shift ;;
    --postgres) DB_URL="${2:-}"; [ -n "$DB_URL" ] || { echo "!! --postgres 需要连接串" >&2; exit 1; }; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "!! 未知参数: $1（-h 看用法）" >&2; exit 1 ;;
  esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# ---- 0) 依赖自检 ----
command -v uv >/dev/null 2>&1 || {
  warn "未找到 uv。请先安装：https://docs.astral.sh/uv/（macOS: brew install uv）"
  exit 1
}
command -v ffmpeg >/dev/null 2>&1 || {
  warn "未找到 ffmpeg；下载转码与本地转录会失败。"
  warn "请先安装：macOS 用 brew install ffmpeg；Ubuntu/Debian 用 sudo apt install ffmpeg"
  exit 1
}
PYVER="$(uv run --no-project python -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo '?')"

HAS_NODE=0
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then HAS_NODE=1; fi
NODE_DESC="未装"
if [ "$HAS_NODE" = "1" ]; then NODE_DESC="已装"; fi

say "环境：uv 就绪（Python ${PYVER}）；Node $NODE_DESC"

# ---- 1) Python 依赖 ----
# worker 的下载/转录/同步是产品核心链路，默认装齐；只有用户显式跳过才裁剪。
EXTRAS="--extra browser --extra transcribe"
[ "${SKIP_BROWSER:-0}" = "1" ] && EXTRAS="--extra transcribe"
[ "${SKIP_TRANSCRIBE:-0}" = "1" ] && EXTRAS="${EXTRAS/--extra transcribe/}"
EXTRAS="$(echo "$EXTRAS" | xargs)"
if [ -n "$DB_URL" ]; then
  EXTRAS="$EXTRAS --extra postgres"
fi
say "[1/5] 安装 Python 依赖（uv sync ${EXTRAS}）"
# --inexact：只**增补**需要的额外依赖，不移除已装的其他 extra（如 dev 的 pytest）。
# 否则在一个开发用的 .venv 里跑本脚本会把 dev 依赖卸掉，破坏开发环境（实测踩过）。
# shellcheck disable=SC2086
uv sync --inexact $EXTRAS
if [ "${SKIP_BROWSER:-0}" != "1" ]; then
  say "      安装 Playwright Chromium（抖音/小红书扫码所需）"
  uv run playwright install chromium
fi

if [ -z "$DB_URL" ]; then
  say "      数据用 SQLite（零依赖），文件放 $DATA_DIR"
  DB_URL="sqlite:///$DATA_DIR/platform.db"
else
  say "      数据用 PostgreSQL：$(echo "$DB_URL" | sed 's#://[^@]*@#://***@#')"
fi
mkdir -p "$DATA_DIR"

# ---- 2) 凭证加密密钥（丢了所有已存凭证作废 → 已存在则不覆盖）----
KEY_FILE="$DATA_DIR/secret.key"
if [ -f "$KEY_FILE" ]; then
  say "[2/5] 复用已有密钥 $KEY_FILE"
else
  say "[2/5] 生成凭证加密密钥 → $KEY_FILE"
  uv run --no-project python - "$KEY_FILE" <<'PY'
import base64, os, sys
path = sys.argv[1]
with open(path, "w") as fh:
    fh.write(base64.b64encode(os.urandom(32)).decode())
os.chmod(path, 0o600)
PY
  say "      ⚠️  请备份这个文件；丢失后已保存的账号凭证全部作废"
fi

# ---- 3) 前端正式包 ----
if [ -f app/static/index.html ] && [ "${SKIP_WEB_BUILD:-0}" = "1" ]; then
  say "[3/5] 跳过前端构建（app/static 已在，SKIP_WEB_BUILD=1）"
elif [ "$HAS_NODE" = "1" ]; then
  say "[3/5] 构建前端正式包（npm ci + vite build → app/static/）"
  # Build into a staging directory and swap only after success. Vite's
  # --emptyOutDir would otherwise delete the working UI before a compile error,
  # leaving users with a server that serves no frontend at all.
  STATIC_STAGE="$(mktemp -d "$ROOT/.app-static-XXXXXX")"
  STATIC_BACKUP="$ROOT/.app-static-backup-$$"
  trap 'rm -rf "$STATIC_STAGE"' EXIT
  (
    cd web
    npm ci --no-audit --no-fund >/dev/null 2>&1 || npm install --no-audit --no-fund >/dev/null 2>&1
    npx vite build --outDir "$STATIC_STAGE" --emptyOutDir
  )
  test -f "$STATIC_STAGE/index.html"
  if [ -e app/static ]; then mv app/static "$STATIC_BACKUP"; fi
  if ! mv "$STATIC_STAGE" app/static; then
    [ ! -e "$STATIC_BACKUP" ] || mv "$STATIC_BACKUP" app/static
    exit 1
  fi
  rm -rf "$STATIC_BACKUP"
  STATIC_BACKUP=""
  trap - EXIT
  unset STATIC_STAGE
else
  warn "[3/5] 未装 Node，无法构建前端。"
  warn "     后端/接口仍可用（PLATFORM_SERVE_FRONTEND=false），但网页打不开。"
  warn "     要网页界面：装 Node 22+ 后重跑本脚本；或让后端只提供 /api。"
  NO_FRONTEND=1
fi

# ---- 4) 数据库迁移 ----
say "[4/5] 迁移数据库到最新（alembic upgrade head）"
# 迁移进程也要用同一个密钥文件；否则新生成的 data/secret.key 与本进程
# 的默认路径不一致时，历史密文会在后续启动校验失败。
PLATFORM_DATABASE_URL="$DB_URL" PLATFORM_DATA_DIR="$DATA_DIR" \
  PLATFORM_CREDENTIAL_KEY_FILE="$DATA_DIR/secret.key" \
  uv run alembic upgrade head

# ---- 5) 启动 ----
SERVE_FRONTEND="true"
[ "${NO_FRONTEND:-0}" = "1" ] && SERVE_FRONTEND="false"

ENV_FILE="${PLATFORM_LOCAL_ENV_FILE:-$ROOT/.env.local-install}"
mkdir -p "$(dirname "$ENV_FILE")"
cat > "$ENV_FILE" <<EOF
# 由 scripts/install-local.sh 生成（无 Docker 安装）。可手工调整后重启。
PLATFORM_HOST=$HOST
PLATFORM_PORT=$PORT
PLATFORM_DATABASE_URL=$DB_URL
PLATFORM_SERVE_FRONTEND=$SERVE_FRONTEND
PLATFORM_DATA_DIR=$DATA_DIR
PLATFORM_CREDENTIAL_KEY_FILE=$KEY_FILE
PLATFORM_AUTO_CREATE_TABLES=false
PLATFORM_RUN_MIGRATIONS=true
EOF

# ---- 常驻服务（可选）：Linux 用 systemd --user，macOS 用 launchd ----
# API 与 worker 必须同时常驻：只有 API 时下载/转录/同步任务会永久排队。
install_service() {
  local PY_BIN
  PY_BIN="$(cd "$ROOT" && uv run python -c 'import sys;print(sys.executable)')"
  if [ "$(uname -s)" = "Darwin" ]; then
    local AGENTS="$HOME/Library/LaunchAgents"
    mkdir -p "$AGENTS" "$DATA_DIR/service-logs"
    local PLIST_API="$AGENTS/com.stargather.platform-api.plist"
    local PLIST_WORKER="$AGENTS/com.stargather.platform-worker.plist"
    cat > "$PLIST_API" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.stargather.platform-api</string>
  <key>ProgramArguments</key><array>
    <string>$PY_BIN</string><string>-m</string><string>uvicorn</string>
    <string>app.main:app</string><string>--host</string><string>$HOST</string>
    <string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict>
    <key>PLATFORM_DATABASE_URL</key><string>$DB_URL</string>
    <key>PLATFORM_SERVE_FRONTEND</key><string>$SERVE_FRONTEND</string>
    <key>PLATFORM_DATA_DIR</key><string>$DATA_DIR</string>
    <key>PLATFORM_CREDENTIAL_KEY_FILE</key><string>$KEY_FILE</string>
    <key>PLATFORM_AUTO_CREATE_TABLES</key><string>false</string>
    <key>PLATFORM_RUN_MIGRATIONS</key><string>true</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DATA_DIR/service-logs/api-out.log</string>
  <key>StandardErrorPath</key><string>$DATA_DIR/service-logs/api-err.log</string>
</dict></plist>
PLIST_EOF
    cat > "$PLIST_WORKER" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.stargather.platform-worker</string>
  <key>ProgramArguments</key><array>
    <string>$PY_BIN</string><string>-m</string><string>app.workers.runner</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict>
    <key>PLATFORM_DATABASE_URL</key><string>$DB_URL</string>
    <key>PLATFORM_DATA_DIR</key><string>$DATA_DIR</string>
    <key>PLATFORM_CREDENTIAL_KEY_FILE</key><string>$KEY_FILE</string>
    <key>PLATFORM_AUTO_CREATE_TABLES</key><string>false</string>
    <key>PLATFORM_RUN_MIGRATIONS</key><string>false</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DATA_DIR/service-logs/worker-out.log</string>
  <key>StandardErrorPath</key><string>$DATA_DIR/service-logs/worker-err.log</string>
</dict></plist>
PLIST_EOF
    launchctl unload "$PLIST_API" 2>/dev/null || true
    launchctl unload "$PLIST_WORKER" 2>/dev/null || true
    launchctl load -w "$PLIST_API"
    launchctl load -w "$PLIST_WORKER"
    say "已注册 launchd 常驻服务（API + worker）"
    echo "     状态： launchctl list | grep stargather"
    echo "     停止： launchctl unload -w $PLIST_API $PLIST_WORKER"
    echo "     日志： $DATA_DIR/service-logs/"
  else
    local UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/stargather-platform-api.service" <<UNIT_EOF
[Unit]
Description=Stargather platform-api (no-docker install)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PLATFORM_DATABASE_URL=$DB_URL
Environment=PLATFORM_SERVE_FRONTEND=$SERVE_FRONTEND
Environment=PLATFORM_DATA_DIR=$DATA_DIR
Environment=PLATFORM_CREDENTIAL_KEY_FILE=$KEY_FILE
Environment=PLATFORM_AUTO_CREATE_TABLES=false
Environment=PLATFORM_RUN_MIGRATIONS=true
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY_BIN -m uvicorn app.main:app --host $HOST --port $PORT
Restart=always
RestartSec=2
TimeoutStopSec=20
NoNewPrivileges=true

[Install]
WantedBy=default.target
UNIT_EOF
    cat > "$UNIT_DIR/stargather-platform-worker.service" <<UNIT_EOF
[Unit]
Description=Stargather platform worker (no-docker install)
After=network-online.target stargather-platform-api.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PLATFORM_DATABASE_URL=$DB_URL
Environment=PLATFORM_DATA_DIR=$DATA_DIR
Environment=PLATFORM_CREDENTIAL_KEY_FILE=$KEY_FILE
Environment=PLATFORM_AUTO_CREATE_TABLES=false
Environment=PLATFORM_RUN_MIGRATIONS=false
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY_BIN -m app.workers.runner
Restart=always
RestartSec=2
TimeoutStopSec=30
NoNewPrivileges=true

[Install]
WantedBy=default.target
UNIT_EOF
    systemctl --user daemon-reload
    systemctl --user enable --now stargather-platform-api.service stargather-platform-worker.service
    say "已注册 systemd 用户服务（API + worker）"
    echo "     状态： systemctl --user status stargather-platform-api stargather-platform-worker"
    echo "     停止： systemctl --user disable --now stargather-platform-api stargather-platform-worker"
    echo "     日志： journalctl --user -u stargather-platform-api -u stargather-platform-worker -f"
    echo "     提示：如要退出登录后仍运行： sudo loginctl enable-linger $USER"
  fi
}

if [ "$AS_SERVICE" = "1" ]; then
  say "[5/5] 注册为常驻服务（$([ "$(uname -s)" = "Darwin" ] && echo launchd || echo systemd)）"
  install_service
  say "服务已启动，等待就绪…"
  for i in $(seq 1 45); do
    if curl -fsS "http://127.0.0.1:$PORT/api/system/version" >/dev/null 2>&1; then
      echo; say "已就绪 ✅  http://127.0.0.1:$PORT/（首次访问设置管理员密码）"
      echo "$(curl -s "http://127.0.0.1:$PORT/api/system/version")"
      exit 0
    fi
    sleep 2
  done
  warn "服务已注册但 90 秒内未就绪，请查日志（见上面的路径）。"
  exit 1
fi

if [ "$NO_START" = "1" ]; then
  say "[5/5] --no-start：已装好未启动。启动命令："
  printf '      API：    set -a; . %q; set +a; uv run uvicorn app.main:app --host %q --port %q\n' "$ENV_FILE" "$HOST" "$PORT"
  printf '      worker： set -a; . %q; set +a; uv run python -m app.workers.runner\n' "$ENV_FILE"
  exit 0
fi

say "[5/5] 以生产模式启动（API + worker，托管前端正式包）"
# 只停本安装目录记录的进程；不能按端口 pkill，否则会杀掉用户同端口的其他服务。
# shellcheck disable=SC1091
. "$ROOT/scripts/lib/local-install.sh"
local_stop
sleep 1
local_start

say "等待就绪…"
for i in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:$PORT/api/system/version" >/dev/null 2>&1; then
    echo
    say "已就绪 ✅"
    echo "     网页：   http://127.0.0.1:$PORT/   （首次访问设置管理员密码）"
    echo "     接口：   http://127.0.0.1:$PORT/api/system/version"
    echo "     日志：   $DATA_DIR/server.log（API）、$DATA_DIR/worker.log（worker）"
    echo "     数据：   $DATA_DIR   （含 SQLite 与 secret.key，备份整个目录）"
    echo "     worker：已在后台启动（下载/转录/同步由它执行）"
    echo
    echo "$(curl -s "http://127.0.0.1:$PORT/api/system/version")"
    exit 0
  fi
  sleep 2
done

warn "90 秒内未就绪。请看日志：$DATA_DIR/server.log"
exit 1
