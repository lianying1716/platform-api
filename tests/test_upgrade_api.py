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
            if "/git/ref/tags/" in getattr(self, "url", ""):
                return {"object": {"type": "commit", "sha": "a" * 40}}
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
            resp = _Resp()
            resp.url = url
            return resp

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    from app.core import version_upgrade

    res = asyncio.run(version_upgrade.check_latest(current="v1.0.0", repo="owner/repo"))
    assert res["latest"] == "v9.9.9"
    assert res["available"] is True
    assert res["url"].startswith("https://example")
    assert res["expected_commit"] == "a" * 40


def test_check_update_resolves_annotated_tag_to_commit(monkeypatch):
    """Annotated tags point at a tag object first; health must compare the commit."""
    import asyncio

    import httpx

    commit = "b" * 40
    tag_object = "c" * 40
    seen: list[str] = []

    class _Resp:
        status_code = 200
        text = ""

        def __init__(self, url: str):
            self.url = url

        def json(self):
            if self.url.endswith("/releases/latest"):
                return {"tag_name": "v9.9.9", "html_url": "https://example/release", "name": "v9.9.9"}
            if self.url.endswith("/git/ref/tags/v9.9.9"):
                return {"object": {"type": "tag", "sha": tag_object, "url": "https://api.github.com/tag-object"}}
            if self.url == "https://api.github.com/tag-object":
                return {"object": {"type": "commit", "sha": commit}}
            raise AssertionError(self.url)

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
            seen.append(url)
            return _Resp(url)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    from app.core import version_upgrade

    res = asyncio.run(version_upgrade.check_latest(current="v1.0.0", repo="owner/repo"))
    assert res["expected_commit"] == commit
    assert any(url.endswith("/git/ref/tags/v9.9.9") for url in seen)
    assert "https://api.github.com/tag-object" in seen


def test_check_update_degrades_cleanly_when_tag_resolution_fails(monkeypatch):
    import asyncio

    import httpx

    class _Resp:
        status_code = 200
        text = ""

        def __init__(self, url: str):
            self.url = url

        def json(self):
            if self.url.endswith("/releases/latest"):
                return {"tag_name": "v9.9.9", "html_url": "https://example/release", "name": "v9.9.9"}
            return {}

        def raise_for_status(self):
            if "/git/ref/tags/" in self.url:
                request = httpx.Request("GET", self.url)
                raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            return _Resp(url)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    from app.core import version_upgrade

    res = asyncio.run(version_upgrade.check_latest(current="v1.0.0", repo="owner/repo"))
    assert res["ok"] is True
    assert res["available"] is True
    assert res["expected_commit"] == ""


def test_public_redirect_fallback_still_resolves_commit(monkeypatch):
    """When only /releases/latest is rate-limited, the refs API can still verify the tag."""
    import asyncio

    import httpx

    commit = "d" * 40
    calls: list[str] = []

    class _ApiResp:
        status_code = 200
        headers = {}
        text = ""

        def __init__(self, url: str):
            self.url = url

        def json(self):
            return {"object": {"type": "commit", "sha": commit}}

        def raise_for_status(self):
            return None

    class _LimitedResp(_ApiResp):
        status_code = 403

        def raise_for_status(self):
            request = httpx.Request("GET", self.url)
            raise httpx.HTTPStatusError("403", request=request, response=httpx.Response(403, request=request))

    class _Redirect:
        status_code = 302
        headers = {"location": "https://github.com/owner/repo/releases/tag/v1.2.3"}
        text = ""
        request = httpx.Request("GET", "https://github.com/owner/repo/releases/latest")

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            calls.append(url)
            if url.endswith("/releases/latest") and "api.github.com" in url:
                return _LimitedResp(url)
            if "/git/ref/tags/" in url:
                return _ApiResp(url)
            return _Redirect()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    from app.core import version_upgrade

    res = asyncio.run(version_upgrade.check_latest(current="v1.0.7", repo="owner/repo"))
    assert res["ok"] is True
    assert res["source"] == "public_redirect"
    assert res["expected_commit"] == commit
    assert any("/git/ref/tags/v1.2.3" in url for url in calls)


def test_check_update_reports_rate_limit_and_token(monkeypatch):
    """Exhausted anonymous quota must be a structured failure, and token auth is used."""
    import asyncio

    import httpx
    from app.config import settings
    from app.core import version_upgrade

    seen = {}

    class _Resp:
        status_code = 403
        headers = {"x-ratelimit-reset": "1792525055"}
        text = "rate limit exceeded"
        url = "https://api.github.com/repos/owner/repo/releases/latest"

        def raise_for_status(self):
            if "/git/ref/tags/" in self.url:
                request = httpx.Request("GET", self.url)
                raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
            request = httpx.Request("GET", "https://api.github.com/repos/owner/repo/releases/latest")
            raise httpx.HTTPStatusError("403", request=request, response=self)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            seen.update(kwargs.get("headers") or {})
            if "api.github.com" in url:
                if "/git/ref/tags/" in url:
                    resp = _Resp()
                    resp.url = url
                    return resp
                resp = _Resp()
                resp.url = url
                return resp
            if "raw.githubusercontent" in url:
                raise AssertionError("commit fallback must use the GitHub API")
            return _Redirect()

    class _Redirect:
        status_code = 302
        headers = {"location": "https://github.com/owner/repo/releases/tag/v1.2.3"}
        text = ""
        request = httpx.Request("GET", "https://github.com/owner/repo/releases/latest")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(settings, "update_github_token", "ghs_test", raising=False)
    result = asyncio.run(version_upgrade.check_latest(current="v1.0.7", repo="owner/repo"))
    assert result["ok"] is True
    assert result["latest"] == "v1.2.3"
    assert result["available"] is True
    assert result["source"] == "public_redirect"
    # Commit lookup is separately best-effort: a rate-limited REST API can also
    # rate-limit the refs endpoint.  Version discovery still succeeds, while the
    # empty commit makes the UI show its explicit "cannot verify commit" warning.
    assert result["expected_commit"] == ""
    assert seen["Authorization"] == "Bearer ghs_test"


def test_check_update_reports_failure_when_redirect_fallback_also_fails(monkeypatch):
    """A private/unavailable release page must still surface an actionable 429/403."""
    import asyncio

    import httpx
    from app.core import version_upgrade

    class _Resp:
        status_code = 429
        headers = {}
        text = "rate limit exceeded"

        def raise_for_status(self):
            request = httpx.Request("GET", "https://api.github.com/repos/owner/repo/releases/latest")
            raise httpx.HTTPStatusError("429", request=request, response=self)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            if "api.github.com" in url:
                return _Resp()
            return httpx.Response(404, request=httpx.Request("GET", url), headers={})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    result = asyncio.run(version_upgrade.check_latest(current="v1.0.7", repo="owner/repo"))
    assert result["ok"] is False
    assert "限流" in result["message"]


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
    assert captured["json"] == {"version": "v9.9.9", "expected_commit": ""}
    assert captured["headers"]["X-Updater-Token"] == "s3cr3t"


def test_upgrade_forwards_expected_release_commit(client, monkeypatch):
    import httpx

    from app.config import settings

    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

        def json(self):
            return {"accepted": True}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(settings, "updater_url", "http://platform-updater:9200", raising=False)
    monkeypatch.setattr(settings, "updater_token", "s3cr3t", raising=False)
    r = client.post(
        "/api/system/upgrade",
        json={"version": "v9.9.9", "expected_commit": "a" * 40},
    )
    assert r.status_code == 200, r.text
    assert captured["json"]["expected_commit"] == "a" * 40


def test_upgrade_rejects_invalid_version(client, monkeypatch):
    """版本号非法 → 422，且绝不调用 updater。"""
    from app.config import settings

    monkeypatch.setattr(settings, "updater_url", "http://platform-updater:9200", raising=False)
    monkeypatch.setattr(settings, "updater_token", "s3cr3t", raising=False)
    r = client.post("/api/system/upgrade", json={"version": "v1.0.0; rm -rf /"})
    assert r.status_code == 422


def test_upgrade_rejects_malformed_expected_commit(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "updater_url", "http://platform-updater:9200", raising=False)
    monkeypatch.setattr(settings, "updater_token", "s3cr3t", raising=False)
    r = client.post(
        "/api/system/upgrade",
        json={"version": "v1.0.0", "expected_commit": "not-a-commit"},
    )
    assert r.status_code == 422
