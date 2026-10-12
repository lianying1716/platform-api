# 让 AI（Claude Code / Codex）用上平台能力

> 面向**第一次接触**的人。全程约 3 分钟，不需要懂代码。
> 目标：让 AI 助手能替你「查账号状态 / 扫码登录 / 建采集任务 / 拿转录」等。

## 最快路径：把提示词直接交给 Agent

如果你在用 Codex / Claude Code，**不用自己敲命令**。打开平台网页 → 「AI 接入」→「MCP 接入」，
按向导填三项，然后点 **「复制给 Agent」**，把整段提示词粘贴进对话框即可。

向导会要求：

| 字段 | 填什么 |
|------|--------|
| 平台地址 | 同一台机器填 `http://127.0.0.1:9100`；Agent 在别的机器填局域网地址，例如 `http://<局域网IP>:9100` |
| API Key | 第 1 步在「API Key」页创建后复制的 `sk-...` |
| platform-api 目录 | 例如 `~/platform-api` |

Agent 收到提示词后会自己完成：安装 MCP 依赖 → 运行自检 → 写客户端配置 → 用 `list_accounts` 验证。
页面下方仍保留手动配置，供 Agent 无法执行终端命令时使用。

---

## 它是怎么工作的（一句话）

平台对外提供一套 **API**（用 API Key 鉴权）。MCP 是一个**小翻译层**，
本地跑起来后，Claude Code / Codex 就能直接调用这套 API —— 你不用手敲任何 HTTP 请求。

```
Claude Code / Codex  ──(MCP 协议)──>  mcp_server（本地小进程）  ──(HTTP + API Key)──>  平台后端
```

---

## 第 1 步：创建 API Key（在网页上点）

1. 打开平台网页（默认 <http://127.0.0.1:9100>）。
2. 左侧进入 **「AI 接入」** 页。
3. 点 **「创建 Key」**：
   - **名称**：随便起，例如 `我的Claude`。
   - **权限范围**：勾 `read`（查询）+ `operate`（建任务/登录）。要改设置再加 `admin`。
   - **限流/分**：默认 60 即可。
4. 点「创建」后，**弹窗里那串 `sk-...` 只会显示这一次**，立刻复制保存。

> 丢了不要紧，回本页「吊销」旧的、重新建一个即可。

---

## 第 2 步：安装依赖（只需一次）

在 `platform-api` 目录下：

```bash
cd ~/platform-api
uv sync --extra mcp          # 安装 MCP SDK（必需）
```

---

## 第 3 步：先自检（强烈建议）

设置两个环境变量后用 `--self-check` 验证「能不能连上后端、Key 有没有效」——
**这一步不启动 MCP**，纯粹排错，比直接接进 Claude Code 再猜要快得多。

```bash
cd ~/platform-api
PLATFORM_API_BASE=http://127.0.0.1:9100 \
PLATFORM_API_KEY=sk-你刚复制的key \
uv run python -m mcp_server.server --self-check
```

看到这样就是通了：

```
MCP server : xsai-platform
API base   : http://127.0.0.1:9100
API key    : 已配置 sk-XXXXXXXX…
工具数      : 14
✅ 连通成功，当前账号数：N
工具清单：
  - check_account
  - create_crawl_task
  ...
```

常见报错：
- `❌ 未配置 PLATFORM_API_KEY` → 环境变量名写错了，必须是 `PLATFORM_API_KEY`。
- `❌ 后端返回 401` → Key 无效或已被吊销，重新在第 1 步建一个。
- `❌ 连不上后端` → 平台没启动，或 `PLATFORM_API_BASE` 地址/端口不对。

> 环境变量两个：
> - `PLATFORM_API_BASE`：后端地址。**Claude Code / Codex 与后端不在同一台机器时**，填后端实际 IP，例如 `http://<局域网IP>:9100`。
> - `PLATFORM_API_KEY`：第 1 步复制的 `sk-...`。

---

## 第 4 步：接进 Claude Code / Codex

### Codex（`~/.codex/config.toml`）

在文件末尾追加（把路径和 key 换成你的）：

```toml
[mcp_servers.xsai-platform]
command = "uv"
args = ["run", "--project", "~/platform-api", "python", "-m", "mcp_server.server"]
cwd = "~/platform-api"

[mcp_servers.xsai-platform.env]
PLATFORM_API_BASE = "http://127.0.0.1:9100"
PLATFORM_API_KEY  = "sk-你复制的key"
```

保存后**重启 Codex**，用 `/mcp` 查看，应能看到 `xsai-platform` 及其工具。

### Claude Code（命令行一条搞定）

```bash
claude mcp add xsai-platform \
  --env PLATFORM_API_BASE=http://127.0.0.1:9100 \
  --env PLATFORM_API_KEY=sk-你复制的key \
  -- uv run --project ~/platform-api python -m mcp_server.server
```

然后在 Claude Code 里输入 `/mcp` 确认连接。

---

## 第 5 步：开始用（直接跟 AI 说人话）

接入后，**不用记任何工具名**，直接说需求即可，例如：

- 「列一下我平台上的账号，哪些掉线了」
- 「帮我扫码登录 B 站」→ AI 会返回二维码，你用手机 App 扫
- 「给抖音建一个关键词『露营』的采集任务」
- 「看看我素材库里最新的 5 条，有没有转录好的」
- 「把素材 3 的内容导出成 markdown」
- 「列出我关注的博主，把 XXX 的同步触发一下」

---

## 可用工具清单（14 个）

| 工具 | 权限 | 用途 |
|------|------|------|
| `list_accounts` | read | 列出所有账号及登录态 |
| `check_account` | operate | 立即体检某账号 |
| `start_qr_login` | operate | 发起扫码登录，拿二维码 |
| `poll_qr_login` | operate | 轮询扫码状态，成功后账号自动入库 |
| `update_cookie` | operate | 手动用 Cookie 录入/更新账号（兜底） |
| `list_followed_creators` | read | 列出关注博主及同步状态 |
| `create_crawl_task` | operate | 建关键词采集任务 |
| `list_crawl_tasks` | read | 列出采集任务 |
| `trigger_sync` | operate | 触发订阅/博主同步 |
| `search_assets` | read | 搜索素材库 |
| `get_transcript` | read | 取某素材转录 |
| `get_analytics` | read | 数据看板摘要 |
| `export_assets` | read | 导出素材转录+分析（md/json） |
| `import_article` | operate | 导入单篇公众号文章 |

> 权限由第 1 步勾选的 scope 决定：只读 Key 调 operate 工具会被拒（403）。

---

## 不想用 MCP？直接调 HTTP 也行

MCP 只是薄封装，能力完全等价。拿到 Key 后可直接用 curl：

```bash
curl -H "Authorization: Bearer sk-你的key" http://127.0.0.1:9100/api/skills/accounts
```

API 交互式文档：<http://127.0.0.1:9100/docs>（所有 `/api/skills/*` 端点）。

---

## 排错速查

| 现象 | 原因 / 处理 |
|------|------|
| Codex/Claude 里看不到工具 | 配置没生效 → 重启客户端；`command` 路径写错 |
| `--self-check` 报 401 | Key 无效/被吊销 → 重建 |
| 报 403 权限不足 | Key 的 scope 不够 → 建 Key 时勾上 `operate`/`admin` |
| 报 429 | 触发限流 → 调大 Key 的「限流/分」 |
| 连不上后端 | 后端没起 / `PLATFORM_API_BASE` 地址错 / 防火墙 |
| 调用记录查不到 | 「AI 接入 → 调用日志」页，`GET /api/ai/call-logs` |

> **安全提醒**：`sk-...` 等同于你的账号权限，**不要提交进 git、不要贴给别人**。
> 平台侧只存哈希，丢了只能重建，这正是为了保护你。
