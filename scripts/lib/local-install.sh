#!/usr/bin/env bash
# Shared runtime controls for no-Docker source installs.
#
# The installer, local backup and local upgrade must agree on the same data
# directory, credential key and process IDs. Reading the generated env file is
# the handoff; guessing from the checkout layout breaks relocated installs.

local_env_file() {
  printf '%s' "${PLATFORM_LOCAL_ENV_FILE:-$ROOT/.env.local-install}"
}

local_service_kind() {
  if systemctl --user cat stargather-platform-api.service >/dev/null 2>&1; then
    printf systemd
  elif [ "$(uname -s)" = "Darwin" ] && launchctl list 2>/dev/null | grep -q com.stargather.platform-api; then
    printf launchd
  else
    printf pid
  fi
}

local_stop_services() {
  case "$(local_service_kind)" in
    systemd) systemctl --user stop stargather-platform-api stargather-platform-worker ;;
    launchd)
      launchctl stop com.stargather.platform-api 2>/dev/null || true
      launchctl stop com.stargather.platform-worker 2>/dev/null || true
      ;;
    *) local_stop ;;
  esac
}

local_start_services() {
  case "$(local_service_kind)" in
    systemd) systemctl --user start stargather-platform-api stargather-platform-worker ;;
    launchd)
      launchctl start com.stargather.platform-api
      launchctl start com.stargather.platform-worker
      ;;
    *) local_start ;;
  esac
}

local_load_env() {
  local file
  file="$(local_env_file)"
  if [ -f "$file" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$file"
    set +a
  fi
  : "${PLATFORM_DATA_DIR:=$ROOT/data}"
  : "${PLATFORM_PORT:=9100}"
  : "${PLATFORM_HOST:=127.0.0.1}"
}

local_data_dir() {
  printf '%s' "${PLATFORM_DATA_DIR:-$ROOT/data}"
}

local_pid_is_ours() {
  local pid="$1" expected="$2"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  case "$(ps -p "$pid" -o args= 2>/dev/null || true)" in
    *"$expected"*) return 0 ;;
    *) return 1 ;;
  esac
}

local_stop_pidfile() {
  local pidfile="$1" expected="$2" pid
  [ -f "$pidfile" ] || return 0
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if local_pid_is_ours "$pid" "$expected"; then
    local child
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
      kill "$child" 2>/dev/null || true
    done
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  : > "$pidfile"
}

local_stop() {
  local data
  local_load_env
  data="$(local_data_dir)"
  local_stop_pidfile "$data/server.pid" "uvicorn app.main:app"
  local_stop_pidfile "$data/worker.pid" "app.workers.runner"
}

local_port_in_use() {
  local host="$1" port="$2"
  python3 - "$host" "$port" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
probe_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.4)
    raise SystemExit(0 if sock.connect_ex((probe_host, port)) == 0 else 1)
PY
}

local_start() {
  local data port host env_file
  env_file="$(local_env_file)"
  [ -f "$env_file" ] || { echo "!! 缺少安装配置 $env_file；请先运行 scripts/install-local.sh" >&2; return 1; }
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
  data="$(local_data_dir)"
  mkdir -p "$data"
  port="${PLATFORM_PORT:-9100}"
  host="${PLATFORM_HOST:-127.0.0.1}"
  if local_port_in_use "$host" "$port"; then
    echo "!! 端口 $port 已被其他进程占用；为避免把旧服务误判为新实例，拒绝启动" >&2
    return 1
  fi
  # Keep the API and worker in a session that survives the invoking shell.
  # Some shells deliver SIGHUP to background jobs on exit; a plain nohup only
  # covers the wrapper process and its children can still be reaped.
  setsid uv run uvicorn app.main:app --host "$host" --port "$port" > "$data/server.log" 2>&1 &
  local api_pid=$!
  echo "$api_pid" > "$data/server.pid"
  setsid uv run python -m app.workers.runner > "$data/worker.log" 2>&1 &
  local worker_pid=$!
  echo "$worker_pid" > "$data/worker.pid"
  # uv may exit quickly when the managed process fails (bad dependency, port
  # race, etc.). Surface that immediately instead of declaring success because
  # some other server answered the health URL.
  sleep 2
  if ! kill -0 "$api_pid" 2>/dev/null || ! kill -0 "$worker_pid" 2>/dev/null; then
    echo "!! API 或 worker 启动后立即退出；日志：" >&2
    echo "   $data/server.log" >&2
    echo "   $data/worker.log" >&2
    return 1
  fi
}
