# platform-api · 统一素材采集管理平台

xsai-workbench 的统一后端主控层 + Web 工作台。设计文档见
[`../docs/workbench-requirements-and-design-v1.md`](../docs/workbench-requirements-and-design-v1.md)。

## 当前进度

- **P1 账号中心**（本仓库）：四平台账号登录态统一管理
  - B站 / 微信公众号：原生扫码登录（纯 HTTP）
  - 抖音 / 小红书：浏览器扫码（Playwright，可选安装）+ 手动粘 Cookie 兜底
  - 凭证 AES-256-GCM 加密存储，前端/API 只见脱敏值
  - 定时体检（默认每 15 分钟），状态：在线/掉线/异常/未检

## 快速开始

开发期数据库使用 PostgreSQL（与生产同构）。本机 5432 若被占用，起一个开发专用容器映射到 5433：

```bash
docker run -d --name platform-pg-dev \
  -e POSTGRES_USER=platform -e POSTGRES_PASSWORD=platform -e POSTGRES_DB=platform \
  -p 5433:5432 -v platform-pg-dev-data:/var/lib/postgresql/data postgres:17-alpine

cd platform-api
uv sync --extra postgres --extra dev          # 安装依赖（Python >= 3.11）
uv run uvicorn app.main:app --port 9100        # 启动后端，首次自动建表
```

打开 `http://127.0.0.1:9100`，首次访问需设置管理员密码。

可选能力：

```bash
uv sync --extra browser && uv run playwright install chromium   # 抖音/小红书浏览器扫码
```

测试：

```bash
uv run pytest tests/ -q
```

前端开发：

```bash
cd web && npm install && npm run dev      # Vite dev server :5173，/api 代理到 :9100
npm run build                              # 产物输出到 ../app/static
```

## 配置

复制 `.env.example` 为 `.env` 后按需修改（`.env` 已被 git 忽略）。**凭证加密密钥丢失即全部凭证作废，迁移机器必须随行。**

环境变量前缀 `PLATFORM_`：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PLATFORM_PORT` | 9100 | 监听端口 |
| `PLATFORM_DATABASE_URL` | 本地 PG `…@127.0.0.1:5433/platform` | 生产配 PostgreSQL DSN |
| `PLATFORM_ACCOUNT_CHECK_INTERVAL` | 15 | 体检间隔（分钟），0 关闭 |
| `PLATFORM_CREDENTIAL_ENC_KEY` | （空） | base64 的 32 字节凭证密钥，生产首选 |
| `PLATFORM_CREDENTIAL_KEY_FILE` | （空） | 密钥文件路径，可置于项目目录外 |

凭证加密密钥来源优先级：环境变量 > 密钥文件 > `data/secret.key`（开发兜底，自动生成）。
**密钥不进 git、迁移机器必须随行，丢了凭证全部作废。**

## 协议来源与许可

各平台适配器协议抄录自工作区内开源项目（保留来源注释）：
bili-sync（MIT）、dysync.net（MIT）、wechat-article-exporter（MIT）、
MediaCrawler（非商业学习许可，已获授权，凭据见 docs）。
