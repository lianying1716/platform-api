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
    # 开发模式：强制不托管前端正式包（前端由 Vite dev server :5173 提供）。
    # 带上 browser extra：抖音/小红书扫码需要 playwright；`uv run` 默认只同步基础依赖，
    # 不带 extra 会漏装 playwright，表现为扫码时报「需要安装 Playwright」。
    PLATFORM_SERVE_FRONTEND=false \
      uv run --extra browser uvicorn app.main:app --host "${PLATFORM_HOST:-127.0.0.1}" --port "${PLATFORM_PORT:-9100}"
    ;;
  preview)
    # 本地预览【已构建的正式包】：显式托管 app/static。需先 ./dev.sh web。
    test -f app/static/index.html || { echo "app/static 还没有前端构建产物，先跑 ./dev.sh web"; exit 1; }
    echo "以正式包模式启动：http://${PLATFORM_HOST:-127.0.0.1}:${PLATFORM_PORT:-9100}"
    PLATFORM_SERVE_FRONTEND=true \
      uv run --extra browser uvicorn app.main:app --host "${PLATFORM_HOST:-127.0.0.1}" --port "${PLATFORM_PORT:-9100}"
    ;;
  worker)
    uv run --extra browser python -m app.workers.runner
    ;;
  web)
    # 正式包构建：产物落 app/static（生产/Docker 用）。
    # 开发阶段默认不跑；必须显式确认（PLATFORM_BUILD_RELEASE=1 或交互输入 yes）。
    if [[ "${PLATFORM_BUILD_RELEASE:-}" != "1" ]]; then
      echo "⚠️  ./dev.sh web 会构建【正式包】并覆盖 app/static/。"
      echo "    开发阶段一般不需要——前端请用：cd web && npm run dev（Vite :5173）。"
      read -r -p "    确定要构建正式包吗？输入 yes 继续：" ans
      [[ "$ans" == "yes" ]] || { echo "已取消。"; exit 1; }
    fi
    # 前端在本机磁盘构建，产物拷回 app/static
    rsync -a --delete --exclude node_modules --exclude dist web/ /tmp/pweb/
    (cd /tmp/pweb && npm install --no-audit --no-fund && npx vite build --outDir /tmp/pweb-dist --emptyOutDir)
    rm -rf app/static && mkdir -p app/static
    cp -R /tmp/pweb-dist/* app/static/
    test -f app/static/index.html && echo "前端已构建并部署到 app/static/（index.html OK）"
    ;;
  *)
    echo "用法: ./dev.sh [sync|serve|worker|preview|web]"
    echo "  serve  后端 API（开发默认，不托管前端）"
    echo "  worker 队列 worker"
    echo "  preview 以正式包模式启动（托管 app/static，需先 ./dev.sh web）"
    echo "  web    构建【正式包】到 app/static（需确认；开发不需要）"
    exit 1
    ;;
esac
