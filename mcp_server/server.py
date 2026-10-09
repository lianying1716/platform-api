"""MCP Server：给 Claude Code / Codex 一键接入。

设计原则（总纲红线"API 即产品"）：MCP 工具是 HTTP API 的薄封装，
不含任何业务逻辑，全部转发到 platform-api 的 /api/skills/*，
用 API Key 鉴权。这样前端、HTTP 客户端、MCP 三条通道能力完全对等。

运行（stdio，给 Claude Code / Codex 用）：
    pip install "mcp>=2" httpx
    PLATFORM_API_BASE=http://127.0.0.1:9100 PLATFORM_API_KEY=sk-xxx \
        python -m mcp_server.server

自检（不启动 MCP，只验证配置与连通性）：
    PLATFORM_API_KEY=sk-xxx python -m mcp_server.server --self-check

工具清单与 app/api/skills.py 的路由一一对应，改一边必须改另一边（有测试守着）。
"""

import os
import sys

import httpx

# MCP SDK 2.x 把 FastMCP 改名为 MCPServer；同时兼容 1.x 的 FastMCP。
try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - 兼容 mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP as _Server
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "需要安装 MCP SDK：pip install 'mcp>=2'\n"
            "MCP 是给 Claude Code/Codex 接入用的，普通 HTTP 客户端无需它。"
        ) from exc

# 默认指向本机后端；Claude Code / Codex 在不同机器运行时改成实际地址。
_BASE = os.environ.get("PLATFORM_API_BASE", "http://127.0.0.1:9100").rstrip("/")
_KEY = os.environ.get("PLATFORM_API_KEY", "")

mcp = _Server("xsai-platform")


def _client() -> httpx.Client:
    if not _KEY:
        raise RuntimeError("未配置 PLATFORM_API_KEY，请在前端「AI 接入」页创建 API Key")
    return httpx.Client(
        base_url=_BASE,
        headers={"Authorization": f"Bearer {_KEY}"},
        timeout=30,
    )


def _get(path: str, **params):
    with _client() as c:
        resp = c.get(path, params={k: v for k, v in params.items() if v is not None})
        resp.raise_for_status()
        return resp.json()


def _post(path: str, body: dict | None = None, params: dict | None = None):
    with _client() as c:
        resp = c.post(path, json=body, params={k: v for k, v in (params or {}).items() if v is not None})
        resp.raise_for_status()
        return resp.json()


# ---------- 账号 ----------
@mcp.tool()
def list_accounts() -> list:
    """列出所有平台账号及其登录态（在线/掉线/异常）。"""
    return _get("/api/skills/accounts")


@mcp.tool()
def check_account(account_id: int) -> dict:
    """对指定账号立即做一次登录态体检。"""
    return _post(f"/api/skills/accounts/{account_id}/check", {})


@mcp.tool()
def start_qr_login(platform: str) -> dict:
    """发起扫码登录，返回二维码图片(base64)和 session_id。
    platform: bilibili / kuaishou / zhihu / douyin / xhs（支持扫码的才可）。
    返回后用 poll_qr_login 轮询状态。"""
    return _post("/api/skills/accounts/qr-login", None, params={"platform": platform})


@mcp.tool()
def poll_qr_login(session_id: str) -> dict:
    """轮询扫码状态：pending/scanned/success/expired。success 后账号自动入库。"""
    return _get(f"/api/skills/accounts/qr-login/{session_id}")


@mcp.tool()
def update_cookie(platform: str, cookie: str, display_name: str = "") -> dict:
    """手动用 Cookie 录入/更新账号（扫码不可用时的兜底）。"""
    return _post("/api/skills/accounts/cookie", {"platform": platform, "cookie": cookie, "display_name": display_name})


# ---------- 采集/同步 ----------
@mcp.tool()
def create_crawl_task(
    platform: str,
    keyword: str,
    sort: str = "popular",
    limit_per_run: int = 50,
    account_id: int = 0,
) -> dict:
    """创建关键词采集任务。account_id=0 表示自动轮换；指定则固定用该账号。"""
    return _post("/api/skills/crawl-tasks", {
        "platform": platform,
        "keyword": keyword,
        "sort": sort,
        "limit_per_run": limit_per_run,
        "account_id": account_id or None,
    })


@mcp.tool()
def list_crawl_tasks() -> list:
    """列出所有关键词采集任务及状态。"""
    return _get("/api/skills/crawl-tasks")


@mcp.tool()
def trigger_sync(subscription_id: int) -> dict:
    """手动触发一个订阅源（收藏夹/关注博主）的同步。"""
    return _post(f"/api/skills/subscriptions/{subscription_id}/sync", {})


@mcp.tool()
def list_followed_creators(platform: str = "", account_id: int = 0) -> list:
    """列出已关注的博主及同步状态（platform: bilibili / douyin）。
    返回的 id 可直接传给 trigger_sync 触发该博主同步。"""
    params = {}
    if platform:
        params["platform"] = platform
    if account_id:
        params["account_id"] = account_id
    return _get("/api/skills/followed-creators", **params)


# ---------- 素材/转录/分析 ----------
@mcp.tool()
def search_assets(platform: str = "", limit: int = 20) -> list:
    """搜索素材库内容（视频/文章及其下载/转录/分析状态）。"""
    return _get("/api/skills/assets", platform=platform or None, limit=limit)


@mcp.tool()
def get_transcript(asset_id: int) -> dict:
    """获取某素材的转录文本（分段+全文）。"""
    return _get(f"/api/skills/assets/{asset_id}/transcript")


@mcp.tool()
def get_analytics() -> dict:
    """获取数据看板摘要（账号、任务、热门内容）。"""
    return _get("/api/skills/analytics")


@mcp.tool()
def export_assets(asset_ids: list[int], format: str = "md") -> dict:
    """把素材的转录+分析导出为 markdown 或 json。"""
    return _post("/api/skills/assets/export", {"asset_ids": asset_ids, "format": format})


@mcp.tool()
def import_article(url: str) -> dict:
    """导入单篇微信公众号文章到素材库（mp.weixin.qq.com/s... 链接）。"""
    return _post("/api/skills/import-article", {"url": url})


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        # 不启动 MCP transport，只验证：能否连上后端 + Key 是否有效 + 工具能否列账号。
        print(f"MCP server : xsai-platform")
        print(f"API base   : {_BASE}")
        print(f"API key    : {'已配置 ' + _KEY[:10] + '…' if _KEY else '❌ 未配置 PLATFORM_API_KEY'}")
        print(f"工具数      : {len(mcp._tool_manager.list_tools())}")
        if not _KEY:
            raise SystemExit("先在前端「AI 接入」页创建 API Key，再设 PLATFORM_API_KEY")
        try:
            accounts = _get("/api/skills/accounts")
        except httpx.HTTPStatusError as exc:
            raise SystemExit(f"❌ 后端返回 {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise SystemExit(f"❌ 连不上后端 {_BASE}：{exc}") from exc
        print(f"✅ 连通成功，当前账号数：{len(accounts)}")
        print("工具清单：")
        for t in sorted(t.name for t in mcp._tool_manager.list_tools()):
            print(f"  - {t}")
        raise SystemExit(0)
    mcp.run()
