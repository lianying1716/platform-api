#!/usr/bin/env bash
# Shared Compose deployment helpers for operational scripts.
#
# Volume names must come from Compose's resolved model.  Deriving them from the
# checkout directory name breaks as soon as the project directory is renamed or
# two instances run on one host.

compose_volume_name() {
  local logical="$1"
  local resolved=""
  resolved="$(docker compose config --format json 2>/dev/null | python3 -c '
import json, sys
try:
    volumes = json.load(sys.stdin).get("volumes") or {}
except Exception:
    raise SystemExit(1)
entry = volumes.get(sys.argv[1]) or {}
print(entry.get("name") or "")
' "$logical" 2>/dev/null || true)"
  if [ -z "$resolved" ]; then
    resolved="$(docker volume ls --format '{{.Name}}' | grep -E "_${logical}$" | head -n 1 || true)"
  fi
  printf '%s' "$resolved"
}

compose_host_port() {
  local port="${PLATFORM_PORT:-}"
  if [ -n "$port" ]; then
    printf '%s' "$port"
    return
  fi
  docker compose config --format json 2>/dev/null | python3 -c '
import json, sys
try:
    ports = json.load(sys.stdin)["services"]["platform-api"].get("ports") or []
except Exception:
    raise SystemExit(1)
published = next((p.get("published") for p in ports if p.get("target") == 9100), "")
print(published or "9100")
' 2>/dev/null || printf '9100'
}

compose_service_env() {
  local service="$1"
  local key="$2"
  local fallback="${3:-}"
  local value=""
  value="$(docker compose config --format json 2>/dev/null | python3 -c '
import json, sys
try:
    env = json.load(sys.stdin)["services"][sys.argv[1]].get("environment") or {}
except Exception:
    raise SystemExit(1)
print(env.get(sys.argv[2], ""))
' "$service" "$key" 2>/dev/null || true)"
  printf '%s' "${value:-$fallback}"
}

compose_app_version() {
  docker compose config --format json 2>/dev/null | python3 -c '
import json, sys
try:
    image = json.load(sys.stdin)["services"]["platform-api"]["image"]
except Exception:
    raise SystemExit(1)
print(image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else "latest")
' 2>/dev/null || true
}
