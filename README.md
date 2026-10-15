<div align="center">

<img src="web/public/logo-symbol.svg" width="88" alt="星采 Stargather">

# 星采 Stargather

**把刷到就收藏、却再也没打开过的视频，变成你自己的内容知识库。**

多平台收藏/关注自动归集 · 本地下载 · 语音转录 · AI 摘要与爆款拆解 · API / MCP 交给你的 Agent。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)
[![Vue](https://img.shields.io/badge/Vue-3-42b883.svg)](web)
[![Deploy](https://img.shields.io/badge/deploy-Docker%20Compose-2496ED.svg)](docker-compose.yml)

[在线演示](https://cai.xsai5.xyz/demo/) · [官网](https://cai.xsai5.xyz/) · [宣传片](#宣传片) · [快速开始](#快速开始)

</div>

---

## 宣传片（v9）

<div align="center">

<!-- GitHub 只对 user-attachments 资源渲染内嵌播放器：指向 raw.githubusercontent 的
     <video> 会被过滤掉，点开只会下载文件。 -->
<video src="https://github.com/user-attachments/assets/b1712f76-5b14-494a-a576-aaf10b782687" controls width="100%"></video>

<sub>约 60 秒（默认静音，点播放器右下角开声音）· [1080p 全片](https://github.com/user-attachments/assets/b0172e09-dea4-41a9-bf11-cf9ed5f0d67e) · [封面帧](https://raw.githubusercontent.com/lianying1716/platform-api/assets/promo/stargather-promo-v9-poster.jpg) · [接触表](https://raw.githubusercontent.com/lianying1716/platform-api/assets/promo/stargather-promo-v9-contact-sheet.jpg)</sub>

</div>

> 片子是按真实界面、真实数据、真视频播放做的（不是自造的 UI mock），制作流程见工作台仓库的 `promo-video/`。

---

## 界面

下面的截图都来自公开演示站（[cai.xsai5.xyz/demo](https://cai.xsai5.xyz/demo/)）的真实页面，
里面是演示数据、账号 ID 与 Cookie 都是脱敏后的占位值。

<table>
<tr>
<td width="50%" align="center"><img src="https://raw.githubusercontent.com/lianying1716/platform-api/assets/shots/overview.jpg" alt="总览"><br><sub><b>总览</b> · 素材总量、时长、账号覆盖</sub></td>
<td width="50%" align="center"><img src="https://raw.githubusercontent.com/lianying1716/platform-api/assets/shots/accounts.jpg" alt="账号中心"><br><sub><b>账号中心</b> · 六平台登录态，掉线自动体检</sub></td>
</tr>
<tr>
<td width="50%" align="center"><img src="https://raw.githubusercontent.com/lianying1716/platform-api/assets/shots/crawl.jpg" alt="关键词采集"><br><sub><b>关键词采集</b> · 按关键词发现作品，门槛触发评论</sub></td>
<td width="50%" align="center"><img src="https://raw.githubusercontent.com/lianying1716/platform-api/assets/shots/assets-videos.jpg" alt="素材库"><br><sub><b>素材库</b> · 按平台 / 账号 / 分类筛选，可播放</sub></td>
</tr>
<tr>
<td width="50%" align="center"><img src="https://raw.githubusercontent.com/lianying1716/platform-api/assets/shots/transcript.jpg" alt="转录与拆解"><br><sub><b>转录与分档加工</b> · 转录、视频理解、爆款拆解</sub></td>
<td width="50%" align="center"><img src="https://raw.githubusercontent.com/lianying1716/platform-api/assets/shots/pipeline.jpg" alt="处理队列"><br><sub><b>处理队列</b> · 下载 / 转录 / 采集任务与进度</sub></td>
</tr>
</table>

---

## 痛点：收藏即遗忘

你在抖音、B站、小红书、快手、知乎、公众号上刷到有用的东西，随手点个收藏，想着「以后再看」。
然后：

- **收藏夹越来越满，回看率越来越低。** 平台推荐流一换，连自己收藏过什么都忘了。
- **账号、关注、收藏散在五六个 App 里**，没有一处能统一查看和管理。
- **素材用不起来。** 视频不能搜、不能引用，想拿去做选题、做对标、喂给自己的模型，
  只能人工复制标题、听写内容、整理成表格。

信息从来不缺，缺的是**留下来、用起来**。

## 解决思路：一条能长期运行的采集底座

星采把「散落在各平台的收藏」收拢成一套**本地、结构化、可被 AI 调用**的内容知识库：

```
登录各平台账号  →  跟踪博主 / 打开收藏点赞 / 关键词定时采集
      →  下载到本地  →  语音转文字  →  AI 摘要 / 标签 / 分型 / 爆款拆解
      →  素材库统一管理  →  通过 API / MCP 交给 Claude Code、Codex 或你自己的 Agent
```

它是**私有化部署**的软件，不是 SaaS：数据库在你这里，素材文件在你这里，平台凭证也在你这里。

## 核心能力

| 能力 | 说明 |
|---|---|
| **账号中心** | B站 / 抖音 / 小红书 / 快手 / 知乎 / 公众号 登录态统一管理，扫码或 Cookie，定时体检掉线 |
| **自动追更** | 导入关注博主，逐博主控制是否同步其名下内容，新作品自动入库 |
| **收藏同步** | 收藏先导入、再按需打开同步开关；账号级总开关可让整个账号进入沉默期 |
| **关键词采集** | 选平台、排序、单次上限与周期，支持热度门槛与评论抓取 |
| **本地下载** | 视频与封面落到你自己的存储根，按账号 / 订阅 / 模板组织目录；支持分P、字幕、弹幕、NFO |
| **转录** | 本地 faster-whisper 生成分段与全文，视频变成可搜索、可引用的文本 |
| **AI 分析** | 接任意 OpenAI 兼容模型，对转录全文做摘要、标签、自动分型 |
| **爆款拆解** | 结合元数据、转录、视频理解与评论摘要，输出结构化拆解报告 |
| **素材库** | 按平台 / 账号 / 分类 / 状态筛选，视频可直接播放，支持导出 Markdown / JSON |
| **任务队列** | 下载 / 转录 / 分析 / 采集各有状态与失败原因，可重试、进度可见 |
| **API / MCP** | API Key 带 scope 与限流；MCP 一行配置接进 Claude Code / Codex |
| **安全** | 凭证 AES-256-GCM 加密入库，前端与 API **永远只返回脱敏值** |

支持平台：`抖音` `B站` `小红书` `快手` `知乎` `微信公众号`

## 快速开始

三种方式：**Docker Compose（推荐）**、**源码运行**、**交给你的 AI Agent 自动部署**。

### 方式 A · Docker Compose（推荐，生产用这个）

```bash
git clone https://github.com/lianying1716/platform-api.git
cd platform-api
cp .env.example .env

# 生成凭证加密密钥（离线保存好，丢了所有已存凭证作废）
python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
# 把输出填进 .env 的 PLATFORM_CREDENTIAL_ENC_KEY=

docker compose up -d          # 起 api + worker + postgres，首次自动迁移建表
```

打开 `http://127.0.0.1:9100`，首次访问设置管理员密码。

- 从源码构建镜像（不走 GHCR）：`docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`
- 网页一键升级（可选）：见 [`.env.example`](.env.example) 的 `PLATFORM_UPDATER_*` 与 [`docs/upgrade-compatibility.md`](docs/upgrade-compatibility.md)
- 升级 / 备份 / 回滚：`./scripts/backup.sh` → `./scripts/upgrade.sh v1.4.0` → 需要时 `./scripts/restore.sh`

### 方式 B · 无 Docker 一键安装（macOS / Linux 裸机）

**不想装 Docker（尤其 macOS）就用这个**：不需要 Docker，也不需要 PostgreSQL——用
[`uv`](https://docs.astral.sh/uv/) 装依赖、SQLite 存数据、构建前端正式包后以生产模式起服务。

```bash
git clone https://github.com/lianying1716/platform-api.git
cd platform-api
./scripts/install-local.sh
```

脚本会：装依赖 → 生成凭证加密密钥（`data/secret.key`，**请备份**）→ 构建前端 →
迁移数据库 → 起服务，最后打印访问地址。默认 `http://127.0.0.1:9100`，首次访问设置管理员密码。

- 只要后端接口、不要网页：`SKIP_WEB_BUILD=1 ./scripts/install-local.sh`（此模式需先有
  `app/static/` 产物；没有 Node 时也可让后端只提供 `/api`）
- 局域网访问：`PLATFORM_HOST=0.0.0.0 ./scripts/install-local.sh`
- 想用 PostgreSQL：`./scripts/install-local.sh --postgres 'postgresql+psycopg2://u:p@host:5432/db'`
- 只装不启动：`--no-start`
- 下载 / 转录 / 同步需要 worker，另开一个终端：
  ```bash
  set -a; . ./.env.local-install; set +a; uv run python -m app.workers.runner
  ```
- 装 Playwright 扫码能力（抖音/小红书）：脚本默认已带 `--extra browser`；跳过用 `SKIP_BROWSER=1`

> 无 Docker 安装的数据都在 `data/`（SQLite + 密钥 + 素材）。备份整个目录即可；
> `secret.key` 丢失 = 所有已存账号凭证作废。生产规模较大时建议改用方式 A 的 PostgreSQL。

### 方式 C · 源码运行（开发 / 单机）

需要 **Python ≥ 3.11**、[`uv`](https://docs.astral.sh/uv/)、Node 22+，以及一个 PostgreSQL。

```bash
# 本地开发库（映射到 5433，与生产同构）
docker run -d --name platform-pg-dev \
  -e POSTGRES_USER=platform -e POSTGRES_PASSWORD=platform -e POSTGRES_DB=platform \
  -p 5433:5432 -v platform-pg-dev-data:/var/lib/postgresql/data postgres:17-alpine

cd platform-api
uv sync --extra postgres --extra dev
uv run uvicorn app.main:app --port 9100          # 后端 API，首次自动建表

# 前端（开发态用 Vite，热更新）
cd web && npm install && npm run dev             # http://127.0.0.1:5173（/api 代理到 9100）
```

worker（下载 / 转录 / 分析）另起一个进程：

```bash
uv run python -m app.workers.runner
```

可选能力：

```bash
uv sync --extra browser && uv run playwright install chromium   # 抖音/小红书 浏览器扫码
uv sync --extra transcribe                                      # 本地 faster-whisper 转录
uv sync --extra mcp                                             # 接入 Claude Code / Codex
```

> **开发 / 正式包隔离**：开发默认 `PLATFORM_SERVE_FRONTEND=false`，后端只提供 `/api`，
> 前端走 Vite `:5173`。只有明确要出正式包时才 `./dev.sh web`（会覆盖 `app/static/`）。

### 方式 D · 交给 AI Agent 自动部署（复制即用）

把下面整段复制给你的编码 Agent（Codex / Claude Code / Cursor）：

```text
请在这台机器上部署「星采 Stargather」：

1. 先看这台机器有没有 Docker：
   - 有（Linux/NAS）：走 Docker，git clone 后 `cp .env.example .env`、
     生成 PLATFORM_CREDENTIAL_ENC_KEY、`docker compose up -d`。
   - 没有（典型是 macOS）：**不要试图装 Docker**，直接用仓库自带的无 Docker 安装脚本。
2. git clone https://github.com/lianying1716/platform-api.git 到当前目录。
3. cd platform-api
4. 没有 Docker 时直接跑：`./scripts/install-local.sh`
   （它用 uv + SQLite 装好并启动，自动生成密钥到 data/secret.key，最后打印访问地址）
   有 Docker 时：`cp .env.example .env` → 生成密钥写进 PLATFORM_CREDENTIAL_ENC_KEY
   （`python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"`）
   → `docker compose up -d`
5. 等就绪后 `curl http://127.0.0.1:9100/api/system/version` 确认版本（应看到 db_in_sync=true）。
6. 打开 http://127.0.0.1:9100 ，提示我设置管理员密码。
7. 把「访问地址 + 用的哪种方式（Docker / 无 Docker）+ 加密密钥位置 + 设置密码步骤」汇总告诉我。

要求：不要改动 .env.example 与 docker-compose.yml 里的安全默认值；
密钥（data/secret.key 或 .env 里的 PLATFORM_CREDENTIAL_ENC_KEY）务必让我另行备份，丢了凭证作废；
不要把我填入的任何 Key / Cookie 写进 git 或日志；完成后把执行过的命令列给我。
```

## AI 接入（把我们变成你 Agent 的工具）

平台对外只有一套 `/api/*`。网页工作台、MCP、外部 HTTP 客户端走的是**同一套能力**，
不存在「只有网页能点」的操作。

- **HTTP API**：标准 REST，任何语言可调，`Authorization: Bearer sk-...` 鉴权。
- **MCP**：本地 stdio 进程，把平台能力转成 14 个工具（`search_assets` / `get_transcript` /
  `create_crawl_task` / `trigger_sync` / `export_assets` …）接进 Claude Code / Codex。
- **API Key** 按 scope 授权（`read` / `operate` / `admin`）+ 限流，每次调用落审计日志。

网页 **「AI 接入」** 页内置「复制给 Agent」向导：填好平台地址、Key、项目目录后一键生成
可直接粘贴的接入提示词。手动配置见 [`docs/mcp-integration.md`](docs/mcp-integration.md)。

## 用哪些大模型

- **分析 / 转录**：接任意 **OpenAI 兼容**接口。网页 **「系统设置 → 大模型」** 填 Base URL、
  模型名、API Key，点「测试连接」即可，**不锁定供应商**；也可以完全离线跑本地模型。
- 想省事的话，直接用作者自营的 **[星算 AI](https://xsai5.xyz)** —— 见下一节。

## 关于星算 AI（作者自营的闭源服务）

星采（本项目）由 **[星算 AI](https://xsai5.xyz)** 的作者开发并开源。
星算是**闭源**的商用大模型服务（`https://xsai5.xyz` / API 网关 `https://api.xsai5.xyz/v1`），
与本仓库是**两个独立的东西**：星采开源、可私有化部署；星算闭源、是可选的外部能力。

两者关系是**可选、非绑定**的：

- 星采**不依赖**星算也能完整运行——任何一个 OpenAI 兼容的模型（本地或云端）都可以。
- 如果你需要现成的多模型网关，可以把星采取向星算：
  - **大模型 / 图像生成**：Base URL 填 `https://api.xsai5.xyz/v1`；
  - 演示站与宣传片里的转录、摘要、拆解、封面，就是用星算上的
    `deepseek-v4.1-flash-free`（分析）与 `qwen-image-2.1`（封面）跑出来的。

> 说明：本项目遵循自身的 `LICENSE`（Apache-2.0）。星算 AI 是独立的闭源服务，
> 与本开源项目之间没有隐含授权或捆绑；是否使用它完全由你决定。

## 部署参考

| 场景 | 推荐方式 | 说明 |
|---|---|---|
| 生产 / 长期运行 | Docker Compose | 见方式 A；`api` 迁移、`worker` 消费队列、`db` 存数据 |
| macOS / 无 Docker 自用 | 一键安装脚本 | 见方式 B；`./scripts/install-local.sh`，SQLite 零依赖 |
| 本地开发 | 源码 + Vite | 见方式 C；后端 `:9100`、前端 `:5173` |
| 单机 / 内网自用 | 源码 + systemd | 模板见 [`deploy/systemd/`](deploy/systemd/) |
| 只想给人看界面 | 静态演示站 | `npm run build:demo` → `web/dist-demo`，nginx 样例 [`deploy/nginx/demo.conf`](deploy/nginx/demo.conf) |

**环境变量**前缀 `PLATFORM_`，完整清单见 [`.env.example`](.env.example)。几个关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PLATFORM_PORT` | `9100` | 监听端口 |
| `PLATFORM_SERVE_FRONTEND` | `false` | 是否由后端托管前端正式包；生产置 `true` |
| `PLATFORM_DATABASE_URL` | 本地 PG `…:5433/platform` | 生产配 PostgreSQL DSN |
| `PLATFORM_CREDENTIAL_ENC_KEY` | 空 | base64 的 32 字节凭证密钥，**生产首选** |
| `PLATFORM_CREDENTIAL_KEY_FILE` | 空 | 密钥文件路径，可置于项目目录外 |
| `PLATFORM_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | 空 / `gpt-4o-mini` | 外部大模型（系统设置里的值优先） |
| `PLATFORM_ACCOUNT_CHECK_INTERVAL` | `15` | 账号体检间隔（分钟），0 关闭 |
| `PLATFORM_RATE_LIMIT_PER_MINUTE` | `12` | 每平台出站限速（次/分钟），保守档 |

凭证加密密钥来源优先级：环境变量 > 密钥文件 > `data/secret.key`（开发兜底，自动生成）。
**密钥不进 git；迁移机器必须随行，丢了凭证全部作废。**

## 架构与约束（开源注记）

- 后端 **FastAPI**，数据库 **PostgreSQL**（本地开发可用 SQLite），部署 **Docker Compose**。
- **单管理员模式**：没有多用户 / 角色体系，一台机器一个人管。
- **一个 API 进程 + 一个 worker 进程**：API 只做轻活，下载 / 转录 / 分析进 worker。
- 任务队列是一张 **PostgreSQL 表**（`SELECT … FOR UPDATE SKIP LOCKED`），**不需要 Redis / Celery**。
- **API 即产品**：前端、MCP、外部客户端共用同一套接口。
- 文件路径一律 `storage_root + relative_path`，外部接口不接受绝对输出路径。

Python 3.11+ / FastAPI + SQLAlchemy 2.0；路由薄、业务在 `app/services/`，平台适配器在 `app/modules/`。

## 项目结构

```
platform-api/
├── app/
│   ├── api/            # FastAPI 路由（accounts / assets / crawl / skills / model_access …）
│   ├── modules/        # 各平台适配器（bilibili / douyin / xhs / kuaishou / zhihu / wechat_mp）
│   ├── services/       # 业务逻辑（采集 / 同步 / 理解 / 拆解 / 模型接入 …）
│   ├── workers/        # 队列 worker 与任务处理器
│   ├── updater/        # 一键升级边车
│   └── core/           # 加密 / 鉴权 / 版本 / 调度等共享原语
├── alembic/            # 数据库迁移
├── web/                # Vue 3 前端（Vite）
├── mcp_server/         # MCP stdio 服务（把平台变成 AI 工具）
├── scripts/            # 备份 / 恢复 / 升级 / 演示数据生产
├── deploy/             # systemd 与 nginx 样例
└── tests/              # pytest（默认 SQLite，迁移类测试需 PostgreSQL）
```

## 测试

```bash
uv run pytest tests/ -q          # 默认 SQLite，覆盖大部分逻辑
# 迁移 / 升级演练测试需要 PostgreSQL（映射 5433），CI 里由 service 容器提供
```

## 协议来源与许可

平台的各平台适配器参考了若干开源项目的公开实现（涉及 B 站、抖音、公众号等），这些项目均为
宽松许可（MIT / Unlicense 等），来源注释保留在对应源码文件的头部；语音转录与媒体下载
分别依赖 faster-whisper 与 yt-dlp。

项目早期还参考过一个非商业许可的采集项目，如今相关代码与资源已全部移除，当前仓库里不含
它的任何内容。前端的平台图标只作识别性使用，商标归各平台所有。第三方清单见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

本项目采用 **Apache-2.0** 许可，见 [`LICENSE`](LICENSE)。

## 免责声明

本项目仅用于**个人学习、研究与自用**。请使用**你自己的账号**，遵守各内容平台的服务条款，
控制采集频率、不要对目标平台造成干扰。使用本项目产生的任何后果由使用者自负。

## 设计文档

架构与设计文档在配套的**工作台仓库** [lianying1716/xsai-workbench](https://github.com/lianying1716/xsai-workbench)（需求、分期计划、品牌资产、宣传片工程）：

- 需求与设计：[`workbench-requirements-and-design-v1.md`](https://github.com/lianying1716/xsai-workbench/blob/main/docs/workbench-requirements-and-design-v1.md)
- 开发总纲：[`development-master-plan.md`](https://github.com/lianying1716/xsai-workbench/blob/main/docs/development-master-plan.md)
- 用途声明与第三方合规：[`license-and-usage-compliance.md`](https://github.com/lianying1716/xsai-workbench/blob/main/docs/license-and-usage-compliance.md)

---

<div align="center"><sub>星采 Stargather · 把收藏，变成你的内容知识库 · 由 <a href="https://xsai5.xyz">星算 AI</a> 的作者开发</sub></div>
