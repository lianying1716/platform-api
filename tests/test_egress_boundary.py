"""Import-time guardrail: business outbound clients must use the egress policy."""

from pathlib import Path

from app.core import egress


ALLOWED_BARE_CLIENTS = {
    # System management endpoints target explicitly configured local/updater
    # services rather than scraped or user supplied URLs.
    "app/api/system.py",
    "app/core/version_upgrade.py",
    # Browser QR pages are fixed platform hosts and use the browser proxy pool.
    "app/modules/browser_qr.py",
    # Platform adapters already wrap their clients through rate_limit/outbound.
    "app/modules/bilibili.py",
    "app/modules/douyin.py",
    "app/modules/crawler/client.py",
    "app/core/rate_limit.py",
    "app/core/egress.py",
}


def test_every_business_outbound_client_declares_egress_policy():
    roots = [Path("app/modules"), Path("app/services")]
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "httpx.AsyncClient(" not in text:
                continue
            rel = path.as_posix()
            if rel in ALLOWED_BARE_CLIENTS:
                continue
            if "client_kwargs(" not in text and "rate_limit.http_client" not in text:
                offenders.append(rel)
    assert offenders == [], f"未声明出站策略的 httpx.AsyncClient: {offenders}"


def test_client_kwargs_returns_proxy_shape(monkeypatch):
    from app.core import outbound

    monkeypatch.setattr(outbound.settings, "outbound_proxy", "http://proxy:8080", raising=False)
    monkeypatch.setattr(outbound.settings, "platform_proxies", {}, raising=False)
    assert egress.client_kwargs("llm") == {"proxy": "http://proxy:8080"}


def test_client_kwargs_honors_platform_opt_out(monkeypatch):
    from app.core import outbound

    monkeypatch.setattr(outbound.settings, "outbound_proxy", "http://proxy:8080", raising=False)
    monkeypatch.setattr(outbound.settings, "platform_proxies", {"llm": "none"}, raising=False)
    assert egress.client_kwargs("llm") == {}


def test_article_download_and_llm_clients_actually_receive_proxy(monkeypatch):
    from app.core import outbound
    import httpx
    import app.modules.article_import as article_import
    import app.modules.media_download as media_download

    monkeypatch.setattr(outbound.settings, "outbound_proxy", "http://proxy:8080", raising=False)
    monkeypatch.setattr(outbound.settings, "platform_proxies", {}, raising=False)
    seen = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            seen.append(kwargs.get("proxy"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *_a, **_k):
            class Resp:
                status_code = 200
                text = "<html></html>"
                headers = {"content-type": "text/html"}
                content = b""
            return Resp()

        def stream(self, *_a, **_k):
            raise AssertionError("not used")

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    import asyncio
    try:
        asyncio.run(article_import.fetch_article("https://mp.weixin.qq.com/s/x"))
    except Exception:
        pass
    async def _exercise_download():
        from pathlib import Path
        return await media_download._download_one(
            "https://cdn.douyin.com/a.mp4", Path("/tmp"), "probe", {}
        )
    try:
        asyncio.run(_exercise_download())
    except Exception:
        pass
    from app.core.egress import client_kwargs
    assert client_kwargs(media_download._platform_of("https://cdn.douyin.com/a.mp4")) == {"proxy": "http://proxy:8080"}
    assert client_kwargs("llm") == {"proxy": "http://proxy:8080"}
    assert seen and all(proxy == "http://proxy:8080" for proxy in seen), seen


def test_ytdlp_receives_proxy(monkeypatch, tmp_path):
    from app.core import outbound
    import app.modules.media_download as media_download

    monkeypatch.setattr(outbound.settings, "outbound_proxy", "http://proxy:8080", raising=False)
    monkeypatch.setattr(outbound.settings, "platform_proxies", {}, raising=False)
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, *_a, **_k):
            return {"id": "x", "title": "t", "duration": 1}

    import sys
    monkeypatch.setitem(sys.modules, "yt_dlp", type("YDL", (), {"YoutubeDL": FakeYDL}))
    media_download._run_download("https://example.com/v", tmp_path, False, "")
    assert seen.get("proxy") == "http://proxy:8080"
