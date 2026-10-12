# platform-api · 统一素材采集管理平台

xsai-workbench 的统一后端主控层 + Web 工作台。

> **本仓库是独立仓库**（https://github.com/lianying1716/platform-api），单独 clone 时
> 下方 `../docs/...` 链接会失效——那些设计文档在**工作台仓库**
> （https://github.com/lianying1716/xsai-workbench）里。工作台内两者是同级目录。

设计文档见工作台仓库的 `docs/`：
[`workbench-requirements-and-design-v1.md`](https://github.com/lianying1716/xsai-workbench/blob/main/docs/workbench-requirements-and-design-v1.md)、
开发总纲 [`development-master-plan.md`](https://github.com/lianying1716/xsai-workbench/blob/main/docs/development-master-plan.md)。

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

生产用 Alembic 迁移（开发期 `init_db()` 仍会自动建表作为兜底）：

```bash
uv run alembic upgrade head
```

测试：

```bash
uv run pytest tests/ -q
```

前端开发（**默认就在开发模式，不需要、也不要构建正式包**）：

```bash
./dev.sh serve              # 后端 API :9100（开发默认，不托管前端）
cd web && npm install && npm run dev      # Vite dev server :5173，/api 代理到 :9100
```

**开发 / 正式包是隔离的**（这是硬约束，见下）：开发时访问 `:5173`（Vite，热更新），
后端只提供 `/api`。只有明确要出正式包时才构建：

```bash
npm run build              # 仅编译校验，产物落 web/dist（不碰正式包目录）
./dev.sh web               # 【正式包】构建到 app/static，需确认
./dev.sh preview           # 以正式包模式本地预览（托管 app/static）
```

### 开发与正式包隔离（勿绕过）

| | 开发（默认） | 正式包 |
|---|---|---|
| 前端来源 | Vite dev server `:5173` | `app/static/` 构建产物 |
| 后端行为 | `PLATFORM_SERVE_FRONTEND=false`，**只提供 `/api`** | `=true`，托管 `app/static` 并做 SPA 回退 |
| 构建命令 | `npm run dev` | `./dev.sh web`（需确认）或 Docker |

- 后端**只有在 `PLATFORM_SERVE_FRONTEND=true` 时**才托管前端；
  开发默认 false，因此不会再出现「存在 `app/static` 就静默托管旧正式包」。
- **开发阶段不要跑 `./dev.sh web` / `npm run build:release`**，除非用户明确确认要出正式包。

## AI 接入（API Key / MCP）

平台对外能力走同一套 `/api/skills/*`，用 **API Key** 鉴权。前端、HTTP 客户端、
Claude Code / Codex（经 MCP）三条通道能力完全对等。

快速开始：

1. 网页左侧 **「AI 接入」** 页创建 API Key（明文只显示一次）。
2. 装 MCP 依赖：`uv sync --extra mcp`
3. 自检连通性（不启动 MCP，纯排错）：

   ```bash
   PLATFORM_API_BASE=http://127.0.0.1:9100 PLATFORM_API_KEY=sk-xxx \
       uv run python -m mcp_server.server --self-check
   ```

4. 按 [`docs/mcp-integration.md`](docs/mcp-integration.md) 把 MCP 接进 Claude Code / Codex。

MCP 是本地 **stdio** 小进程（`mcp_server/server.py`），不是 HTTP 端点；它把 AI 的调用
转发到本平台的 `/api/skills/*`。共 14 个工具，全部调用落 `api_call_log` 可审计。

### 平台自己的大模型（内容分析用）

「AI 接入」有两层，别混淆：

1. **别人调我们**（上面这节）：外部 AI 通过 API Key / MCP 调平台能力。
2. **我们调大模型**（本节）：平台做摘要 / 视频理解 / 爆款拆解时调用的外部 LLM。

第 2 项的接入方法：网页左侧 **「系统设置 → 大模型」** 填 Base URL、模型名、API Key
（OpenAI 兼容接口），点 **「测试连接」** 校验——通过后再保存即可。
字段说明：Base URL 填到 `/v1` 为止；Key 只写入不回显；留空的模型名默认 `gpt-4o-mini`。
也可用环境变量 `PLATFORM_LLM_BASE_URL` / `PLATFORM_LLM_API_KEY` / `PLATFORM_LLM_MODEL`，
但**系统设置里的值优先**于环境变量。

## 配置

复制 `.env.example` 为 `.env` 后按需修改（`.env` 已被 git 忽略）。**凭证加密密钥丢失即全部凭证作废，迁移机器必须随行。**

环境变量前缀 `PLATFORM_`：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PLATFORM_PORT` | 9100 | 监听端口 |
| `PLATFORM_SERVE_FRONTEND` | false | 是否托管前端正式包 `app/static`；生产置 true |
| `PLATFORM_DATABASE_URL` | 本地 PG `…@127.0.0.1:5433/platform` | 生产配 PostgreSQL DSN |
| `PLATFORM_ACCOUNT_CHECK_INTERVAL` | 15 | 体检间隔（分钟），0 关闭 |
| `PLATFORM_CREDENTIAL_ENC_KEY` | （空） | base64 的 32 字节凭证密钥，生产首选 |
| `PLATFORM_CREDENTIAL_KEY_FILE` | （空） | 密钥文件路径，可置于项目目录外 |
| `PLATFORM_RATE_LIMIT_PER_MINUTE` | 12 | 每平台出站请求限速（次/分钟），保守档 |
| `PLATFORM_DOWNLOAD_MIN_GAP_SECONDS` | 4 | 相邻下载最小间隔（秒） |
| `PLATFORM_OUTBOUND_PROXY` | （空） | 可选全局出站代理，如 `http://user:pass@host:port`；默认直连 |
| `PLATFORM_PLATFORM_PROXIES` | （空） | 按平台代理 JSON，如 `{"douyin":"http://a:1","bilibili":"none"}`；`none`=强制直连 |
| `PLATFORM_AUTO_MIGRATE` | true | 启动版本门禁允许自动升级到 head；只读实例设 false |
| `PLATFORM_ALLOW_UNKNOWN_DB` | false | 逃生开关：允许带未知 revision 启动，仅人工排障 |
| `PLATFORM_RUN_MIGRATIONS` | true | 本容器是否执行迁移；worker 置 false（迁移单飞） |

出站代理默认关闭（单机自用通常直连即可）。需要隔离出口 IP 时再配置：`PLATFORM_PLATFORM_PROXIES`
按平台覆盖 `PLATFORM_OUTBOUND_PROXY`，HTTP 抓取、受管浏览器与扫码会话都会走同一代理。

凭证加密密钥来源优先级：环境变量 > 密钥文件 > `data/secret.key`（开发兜底，自动生成）。
**密钥不进 git、迁移机器必须随行，丢了凭证全部作废。**

## 版本与升级

版本信息见 `GET /api/system/version`（应用版本 + 数据库 revision + 是否同步），
诊断页也会展示。升级走镜像 tag 驱动，**升级前强制备份，回滚靠恢复备份**（不做 downgrade）：

```bash
./scripts/backup.sh            # 强制备份（DB + /data 卷，含 secret.key）
./scripts/upgrade.sh v1.3.0    # 拉镜像并按序升级，等待健康检查
./scripts/restore.sh backups/backup-*.tar.gz   # 需要回滚时
```

升级系统的保证契约、兼容矩阵与排障见 [`docs/upgrade-compatibility.md`](docs/upgrade-compatibility.md)：
**要么完整成功，要么在对外服务前失败并保持升级前状态**，不会出现"迁移做了一半带着坏数据运行"。

## 公众号能力边界

- ✅ 登录态管理（扫码/探活）、✅ 单篇文章 URL 导入（`POST /api/imports/wechat-article`）。
- ❌ 不做公众号订阅同步 / 批量历史文章列表 / 公开索引发现：微信已于 2026-07 关闭相关后台接口。
  详见工作台仓库 [`docs/plans/2026-10-05-wechat-mp-investigation.md`](https://github.com/lianying1716/xsai-workbench/blob/main/docs/plans/2026-10-05-wechat-mp-investigation.md)。

## 协议来源与许可

各平台适配器协议抄录自工作区内开源项目（保留来源注释）：
bili-sync（MIT）、dysync.net（MIT）、wechat-article-exporter（MIT）、
MediaCrawler（非商业学习许可，已获授权，凭据见 docs）。
