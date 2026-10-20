"""SSRF boundary for remote image URLs."""

import asyncio
import io

import pytest
from PIL import Image

from app.core import egress


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x.jpg",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/x.jpg",
    "http://192.168.1.2/x.jpg",
    "http://[::1]/x.jpg",
    "http://localhost/x.jpg",
])
def test_private_and_metadata_urls_are_rejected(monkeypatch, url):
    called = False

    def fake_getaddrinfo(*_args, **_kwargs):
        nonlocal called
        called = True
        host = url.split("/")[2].strip("[]")
        address = {"localhost": "127.0.0.1"}.get(host, host)
        return [(2, 1, 6, "", (address, 80))]

    monkeypatch.setattr(egress.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(egress.EgressBlocked):
        asyncio.run(egress.fetch_image(url))


def test_hostname_resolving_to_private_address_is_rejected(monkeypatch):
    monkeypatch.setattr(
        egress.socket, "getaddrinfo",
        lambda *_a, **_k: [(2, 1, 6, "", ("10.0.0.7", 80))],
    )
    with pytest.raises(egress.EgressBlocked, match="内网"):
        asyncio.run(egress.fetch_image("http://private.example/x.jpg"))


def test_redirect_hops_are_checked_and_redirect_loop_is_bounded(monkeypatch):
    hosts = []

    def fake_resolve(url):
        host = egress.urlsplit(url).hostname
        hosts.append(host)
        if host == "public.example":
            return host, "93.184.216.34"
        return host, "127.0.0.1"

    class FakeResponse:
        status_code = 302
        headers = {"location": "http://127.0.0.1/internal"}
        content = b""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def send(self, _request):
            return FakeResponse()

    monkeypatch.setattr(egress, "_resolve_public", fake_resolve)
    monkeypatch.setattr(egress.httpx, "AsyncClient", lambda **_kw: FakeClient())
    with pytest.raises(egress.EgressBlocked):
        asyncio.run(egress.fetch_image("http://public.example/x.jpg"))
    assert hosts == ["public.example"] + ["127.0.0.1"] * 5


def test_success_pins_public_ip_and_returns_image(monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (255, 0, 0)).save(buffer, format="PNG")
    png = buffer.getvalue()

    connected = {}

    class Backend:
        def __init__(self, address):
            connected["address"] = address

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = png

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def send(self, request):
            assert request.url.host == "public.example"
            return FakeResponse()

    monkeypatch.setattr(egress, "_resolve_public", lambda _u: ("public.example", "93.184.216.34"))
    monkeypatch.setattr(egress, "_PinnedBackend", Backend)
    monkeypatch.setattr(egress.httpx, "AsyncClient", lambda **_kw: FakeClient())
    response = asyncio.run(egress.fetch_image("https://public.example/x.png"))
    assert response.content == png
    assert response.content_type == "image/png"
    assert connected["address"] == "93.184.216.34"


@pytest.mark.parametrize("content_type", ["text/html", "image/svg+xml", "application/javascript", ""])
def test_non_image_content_types_are_never_proxied(monkeypatch, content_type):
    class FakeResponse:
        status_code = 200
        headers = {"content-type": content_type} if content_type else {}
        content = b"<script>alert(1)</script>"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def send(self, _request):
            return FakeResponse()

    monkeypatch.setattr(egress, "_resolve_public", lambda _u: ("public.example", "93.184.216.34"))
    monkeypatch.setattr(egress, "_PinnedBackend", lambda _address: object())
    monkeypatch.setattr(egress.httpx, "AsyncClient", lambda **_kw: FakeClient())
    with pytest.raises(egress.EgressContentTypeBlocked):
        asyncio.run(egress.fetch_image("https://public.example/not-an-image"))


def test_oversize_image_is_rejected(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}
        content = b"x" * 32

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def send(self, _request):
            return FakeResponse()

    monkeypatch.setattr(egress, "_resolve_public", lambda _u: ("public.example", "93.184.216.34"))
    monkeypatch.setattr(egress.httpx, "AsyncClient", lambda **_kw: FakeClient())
    with pytest.raises(egress.EgressResponseTooLarge):
        asyncio.run(egress.fetch_image("https://public.example/x.jpg", max_bytes=16))


def test_remote_image_with_matching_mime_but_non_image_bytes_is_rejected(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = b"<html><script>alert(1)</script></html>"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def send(self, _request):
            return FakeResponse()

    monkeypatch.setattr(egress, "_resolve_public", lambda _u: ("public.example", "93.184.216.34"))
    monkeypatch.setattr(egress, "_PinnedBackend", lambda _address: object())
    monkeypatch.setattr(egress.httpx, "AsyncClient", lambda **_kw: FakeClient())
    with pytest.raises(egress.EgressContentTypeBlocked, match="签名"):
        asyncio.run(egress.fetch_image("https://public.example/disguised.png"))


def test_media_cache_revalidates_legacy_entries_and_relabels_from_signature(tmp_path, monkeypatch):
    from app.core import media_cache

    monkeypatch.setattr(media_cache.settings, "data_dir", tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (0, 255, 0)).save(buffer, format="PNG")
    png = buffer.getvalue()
    # Simulate a pre-fix entry with a supported but wrong raster declaration.
    media_cache.write("legacy", 1, content_type="image/jpeg", body=png)
    cached = media_cache.read("legacy", 1)
    assert cached is not None
    assert cached[0] == "image/png"

    key = media_cache._key("legacy", 2)
    (tmp_path / "media_cache" / f"{key}.json").write_text('{"type":"image/png"}', "utf-8")
    (tmp_path / "media_cache" / f"{key}.bin").write_bytes(b"<svg><script>alert(1)</script></svg>")
    assert media_cache.read("legacy", 2) is None


def test_cover_proxy_rejects_private_url_without_connecting(client, session, tmp_path, monkeypatch):
    from app.models import ContentItem, MediaAsset, PlatformAccount
    import app.api.assets as assets_mod

    monkeypatch.setattr(assets_mod, "_cover_cache_dir", lambda: tmp_path / "cover-cache")
    account = PlatformAccount(platform="douyin", display_name="ssrf")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="douyin",
        external_id="ssrf-cover",
        cover_url="http://127.0.0.1/private.jpg",
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id)
    session.add(asset)
    session.commit()

    response = client.get(f"/api/assets/{asset.id}/cover")
    assert response.status_code == 422
    assert "不允许访问" in response.json()["detail"]
