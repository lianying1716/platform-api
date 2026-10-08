"""外部 Skill API 与 MCP 工具的契约测试。

守两条线，避免"UI 说有、实际 405"这类漂移：
1. **路由存在性**：每个 skills 端点都必须真实存在且方法匹配
   （历史 bug：13 个 MCP 工具里 7 个打到 405 / 错误路径）。
2. **MCP 源文件与 API 对齐**：解析 mcp_server/server.py 里的 /api/skills 路径，
   逐个在 app.routes 里核对，改一边忘改另一边直接失败。
"""

import re
from pathlib import Path

import pytest

SERVER_PY = Path(__file__).resolve().parents[1] / "mcp_server" / "server.py"


@pytest.fixture(autouse=True)
def _reset_rate_window():
    from app.core import api_auth

    api_auth._rate_window.clear()
    yield
    api_auth._rate_window.clear()


def _make_key(client, scopes=("read", "operate")):
    r = client.post("/api/ai/keys", json={"name": "surface-test", "scopes": list(scopes)})
    assert r.status_code == 200, r.text
    return r.json()["api_key"]


def _route_map():
    from app.main import app

    out = {}
    for r in app.routes:
        if hasattr(r, "methods") and hasattr(r, "path") and r.path.startswith("/api/skills"):
            out.setdefault(r.path, set()).update(m for m in r.methods if m != "HEAD")
    return out


def _norm(p):
    return re.sub(r"\{[^}]+\}", "{p}", p)


def test_mcp_source_references_only_real_routes():
    """mcp_server/server.py 里写死的 /api/skills 路径必须都能在 app 里找到。"""
    src = SERVER_PY.read_text(encoding="utf-8")
    paths = set(re.findall(r'"(/api/skills[^"]*)"', src))
    route_norms = {_norm(p) for p in _route_map()}
    missing = [p for p in paths if _norm(p) not in route_norms]
    assert not missing, f"MCP server 引用了不存在的 skills 路由: {missing}"


def test_mcp_tools_all_registered():
    """MCP server 必须能导入并注册全部工具（防 SDK 版本/语法导致启动即崩）。"""
    pytest.importorskip("mcp")
    import os

    os.environ.setdefault("PLATFORM_API_KEY", "dummy-for-import")
    from mcp_server import server

    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    required = {
        "list_accounts", "check_account", "start_qr_login", "poll_qr_login",
        "update_cookie", "create_crawl_task", "list_crawl_tasks", "trigger_sync",
        "list_followed_creators", "search_assets", "get_transcript",
        "get_analytics", "export_assets", "import_article",
    }
    assert required <= names, f"缺少 MCP 工具: {required - names}"


def test_every_skill_route_reachable_with_key(client):
    """每个 skills 路由都不能是 405（方法/路径存在性）。"""
    raw = _make_key(client)
    H = {"Authorization": f"Bearer {raw}"}
    probes = [
        ("GET", "/api/skills/accounts", None, None),
        ("POST", "/api/skills/accounts/1/check", {}, None),
        ("POST", "/api/skills/accounts/qr-login", None, {"platform": "bilibili"}),
        ("GET", "/api/skills/accounts/qr-login/nope", None, None),
        ("POST", "/api/skills/accounts/cookie", {"platform": "bilibili", "cookie": "SESSDATA=a; bili_jct=b"}, None),
        ("POST", "/api/skills/crawl-tasks", {"platform": "douyin", "keyword": "x"}, None),
        ("GET", "/api/skills/crawl-tasks", None, None),
        ("POST", "/api/skills/subscriptions/1/sync", {}, None),
        ("GET", "/api/skills/followed-creators", None, None),
        ("GET", "/api/skills/assets", None, {"limit": 5}),
        ("GET", "/api/skills/assets/1/transcript", None, None),
        ("GET", "/api/skills/analytics", None, None),
        ("POST", "/api/skills/assets/export", {"asset_ids": [1], "format": "md"}, None),
        # 非微信域名：在触网前就被 422 拦下，用于验证路由存在性而不联网
        ("POST", "/api/skills/import-article", {"url": "https://example.com/s/x"}, None),
    ]
    for method, path, body, params in probes:
        if method == "GET":
            r = client.get(path, headers=H, params=params)
        else:
            r = client.post(path, json=body, headers=H, params=params)
        assert r.status_code != 405, f"{method} {path} -> 405（路由不存在或方法不匹配）"
