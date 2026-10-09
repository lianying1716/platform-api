#!/usr/bin/env bash
# 本机开发启动脚本。
# 注意：本项目位于挂载卷（/mnt/wwwroot）时，.venv 和 node_modules 必须放本机磁盘，
# 否则 macOS 的 ._ 文件会破坏 wheel 安装、且 I/O 慢到不可用。
set -euo pipefail
cd "$(dirname "$0")"

export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/platform-api}"

case "${1:-serve}" in
  sync)
    uv sync --extra browser
    uv run playwright install chromium
    ;;
  serve)
    # 带上 browser extra：抖音/小红书扫码需要 playwright；`uv run` 默认只同步基础依赖，
    # 不带 extra 会漏装 playwright，表现为扫码时报「需要安装 Playwright」。
    uv run --extra browser uvicorn app.main:app --host "${PLATFORM_HOST:-127.0.0.1}" --port "${PLATFORM_PORT:-9100}"
    ;;
  worker)
    uv run --extra browser python -m app.workers.runner
    ;;
  web)
    # 前端在本机磁盘构建，产物拷回 app/static
    rsync -a --delete --exclude node_modules --exclude dist web/ /tmp/pweb/
    (cd /tmp/pweb && npm install --no-audit --no-fund && npx vite build --outDir /tmp/pweb-dist --emptyOutDir)
    rm -rf app/static && mkdir -p app/static
    cp -R /tmp/pweb-dist/* app/static/
    test -f app/static/index.html && echo "前端已构建并部署到 app/static/（index.html OK）"
    ;;
  *)
    echo "用法: ./dev.sh [sync|serve|worker|web]"
    exit 1
    ;;
esac
