# platform-api · Agent 指引

本仓库子目录优先于上级 `AGENTS.md`。**部署这台机器时先读这一节。**

## 部署：先判断有没有 Docker

| 机器情况 | 用什么 |
|---|---|
| 有 Docker（Linux 服务器 / NAS） | `cp .env.example .env` → 生成 `PLATFORM_CREDENTIAL_ENC_KEY` → `docker compose up -d` |
| **没有 Docker（典型是 macOS）** | **`./scripts/install-local.sh`** —— 不要试图安装 Docker |

`./scripts/install-local.sh` 是这个仓库给「无 Docker 环境」的一等安装入口：
用 `uv` 装依赖、SQLite 存数据、构建前端正式包、以生产模式起服务。
常用变体：

- `./scripts/install-local.sh --service` —— 装好并注册常驻服务（systemd --user / launchd），开机自启
- `PLATFORM_HOST=0.0.0.0 ./scripts/install-local.sh` —— 局域网可访问
- `--no-start` 只装不启动；`SKIP_WEB_BUILD=1` 跳过前端构建；`--postgres '<dsn>'` 改用 PostgreSQL

装完在 `data/`（SQLite + `secret.key` + 素材），`secret.key` 丢失 = 所有已存凭证作废，
**务必提醒用户另行备份**。下载 / 转录 / 同步需要 worker 另起一个进程
（`set -a; . ./.env.local-install; set +a; uv run python -m app.workers.runner`）。

不要「让 agent 自己临场发挥」拼一堆 `uv sync` / `uvicorn` 命令——路径固定走脚本，行为才可复现。

## 开发约定

- 后端 FastAPI + SQLAlchemy 2.0；测试 `uv run pytest tests/ -q`（裸跑必绿，不依赖真实 PostgreSQL）。
- 前端 Vue 3 + Vite，在 `web/`；`npm run dev`（:5173，代理 /api 到 :9100）；
  正式包 `npm run build:release`（产物落 `app/static/`，开发阶段别乱跑）。
- **开发默认 `PLATFORM_SERVE_FRONTEND=false`**：后端只提供 `/api`，前端走 Vite。
- 迁移：`uv run alembic upgrade head`；新库建表交给 Alembic，不要 `create_all`。
- 凭证一律 AES-GCM 加密入库、接口响应永远脱敏；密钥不进 git。

## 发布（硬规则：未经用户明确确认不得发布）

`main` 是唯一主线；打 `vX.Y.Z` tag 推送才会触发 `.github/workflows/release.yml`
构建多架构镜像并建 GitHub Release —— 这就是「发布」。版本号由用户定，不要自行递增。
发版后按上级 `AGENTS.md` 在 NAS 上做「更新 + 功能验证」。
