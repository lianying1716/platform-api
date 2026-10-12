"""前端"检查更新"与"一键升级"后端接口（对应设计 §3.6 的 Web 入口）。

- 检查更新：读 GitHub Releases（httpx mock，不联网），比对当前版本。
- 触发升级：仅当配置了 updater 边车时可用；版本号必须过白名单校验。
"""

import pytest


def test_check_update_reports_newer(monkeypatch):
    """远端有更高版本时 available=True。"""
    import asyncio

    import httpx

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"tag_name": "v9.9.9", "html_url": "https://example/release", "name": "v9.9.9"}

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    from app.core import version_upgrade

    res = asyncio.run(version_upgrade.check_latest(current="v1.0.0", repo="owner/repo"))
    assert res["latest"] == "v9.9.9"
    assert res["available"] is True
    assert res["url"].startswith("https://example")


def test_version_whitelist_rejects_injection():
    """版本号白名单：拒绝 shell 注入 / 路径穿越。"""
    from app.core import version_upgrade

    for bad in ["v1.0.0; rm -rf /", "v1.0.0 && curl evil", "../../etc/passwd", "latest", ""]:
        assert version_upgrade.is_valid_version(bad) is False, bad
    for ok in ["v1.2.3", "v1.2.3-rc.1", "1.2.3"]:
        assert version_upgrade.is_valid_version(ok) is True, ok


def test_upgrade_disabled_without_updater_url(client, monkeypatch):
    """未配置 updater 时，触发升级应 400/409 并明确说明。"""
    from app.config import settings

    monkeypatch.setattr(settings, "updater_url", "", raising=False)
    r = client.post("/api/system/upgrade", json={"version": "v9.9.9"})
    assert r.status_code in (400, 409)
    assert "未启用" in r.text or "updater" in r.text.lower()


def test_upgrade_calls_updater_when_enabled(client, monkeypatch):
    """配置 updater 后，POST 升级应带共享密钥调边车并回传结果。"""
    import httpx

    from app.config import settings

    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

        def json(self):
            return {"ok": True, "version": "v9.9.9", "status": "started"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured.update({"url": url, "json": json, "headers": headers})
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(settings, "updater_url", "http://platform-updater:9200", raising=False)
    monkeypatch.setattr(settings, "updater_token", "s3cr3t", raising=False)

    r = client.post("/api/system/upgrade", json={"version": "v9.9.9"})
    assert r.status_code == 200, r.text
    assert captured["url"].endswith("/upgrade")
    assert captured["json"] == {"version": "v9.9.9"}
    assert captured["headers"]["X-Updater-Token"] == "s3cr3t"


def test_upgrade_rejects_invalid_version(client, monkeypatch):
    """版本号非法 → 422，且绝不调用 updater。"""
    from app.config import settings

    monkeypatch.setattr(settings, "updater_url", "http://platform-updater:9200", raising=False)
    monkeypatch.setattr(settings, "updater_token", "s3cr3t", raising=False)
    r = client.post("/api/system/upgrade", json={"version": "v1.0.0; rm -rf /"})
    assert r.status_code == 422
