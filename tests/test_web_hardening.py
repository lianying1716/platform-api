"""Browser security boundary tests for the API and the rendered article HTML."""

from sqlalchemy import select


def test_security_headers_are_present(client):
    response = client.get("/api/auth/status")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_article_images_use_server_proxy_and_private_sources_are_removed(client, session):
    from app.models import ContentItem, MediaAsset, PlatformAccount

    account = PlatformAccount(platform="wechat_mp", display_name="test")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="wechat_mp",
        external_id="article-images",
        content_type="article",
        title="images",
        extra={
            "html": (
                '<p>正文</p>'
                '<img src="http://127.0.0.1:9222/json/version">'
                '<img src="https://mmbiz.qpic.cn/a.jpg">'
            )
        },
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id)
    session.add(asset)
    session.commit()

    body = client.get(f"/api/assets/{asset.id}/article").json()
    html = body["html"]
    assert "127.0.0.1" not in html
    assert "/api/articles/" in html
    assert "https://mmbiz.qpic.cn/a.jpg" not in html


def test_avatar_proxy_serves_archive_avatar_without_remote_fetch(client, session, tmp_path, monkeypatch):
    """Legacy archive avatars are same-origin files, not remote URLs."""
    from PIL import Image

    from app.config import settings
    from app.models import CreatorProfile

    archive = tmp_path / "archive_avatars"
    archive.mkdir()
    Image.new("RGB", (2, 2), (255, 0, 0)).save(archive / "dy_local.jpg", format="JPEG")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    profile = CreatorProfile(
        platform="douyin",
        author_id="archive-1",
        author_name="归档头像",
        avatar="/api/archive/avatar/dy_local.jpg",
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)

    import app.api.media as media_mod

    async def refuse_remote(*_a, **_k):
        raise AssertionError("local archive avatar must not use the remote egress client")

    monkeypatch.setattr(media_mod, "fetch_image", refuse_remote)
    response = client.get(f"/api/media/avatars/{profile.id}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert response.content.startswith(b"\xff\xd8\xff")


def test_avatar_proxy_rejects_archive_path_escape(client, session, tmp_path, monkeypatch):
    from app.config import settings
    from app.models import CreatorProfile

    archive = tmp_path / "archive_avatars"
    archive.mkdir()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    profile = CreatorProfile(
        platform="douyin",
        author_id="archive-2",
        author_name="越界",
        avatar="/api/archive/avatar/../secret.jpg",
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    assert client.get(f"/api/media/avatars/{profile.id}").status_code == 422


def test_avatar_proxy_rejects_encoded_archive_path_escape(client, session, tmp_path, monkeypatch):
    from app.config import settings
    from app.models import CreatorProfile

    (tmp_path / "archive_avatars").mkdir()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    profile = CreatorProfile(
        platform="douyin",
        author_id="archive-3",
        author_name="编码越界",
        avatar="/api/archive/avatar/%2e%2e%2fsecret.jpg",
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    assert client.get(f"/api/media/avatars/{profile.id}").status_code == 422


def test_avatar_proxy_returns_404_for_missing_archive_avatar(client, session, tmp_path, monkeypatch):
    from app.config import settings
    from app.models import CreatorProfile

    (tmp_path / "archive_avatars").mkdir()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    profile = CreatorProfile(
        platform="douyin",
        author_id="archive-4",
        author_name="缺失头像",
        avatar="/api/archive/avatar/missing.jpg",
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    assert client.get(f"/api/media/avatars/{profile.id}").status_code == 404


def test_article_image_proxy_rejects_private_target(client, session, tmp_path, monkeypatch):
    from app.models import ContentItem, MediaAsset, PlatformAccount
    import app.core.media_cache as media_cache_mod

    monkeypatch.setattr(media_cache_mod.settings, "data_dir", tmp_path)
    account = PlatformAccount(platform="wechat_mp", display_name="test")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="wechat_mp",
        external_id="article-proxy",
        content_type="article",
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id)
    session.add(asset)
    session.commit()

    response = client.get(f"/api/articles/{asset.id}/images/0")
    assert response.status_code == 404


def test_article_image_index_matches_render_after_unsafe_source_is_removed(
    client, session, tmp_path, monkeypatch
):
    """Indexes must be assigned after sanitizing, exactly as the article renderer does."""
    from bs4 import BeautifulSoup

    import app.core.egress as egress_mod
    import app.core.media_cache as media_cache_mod
    from app.models import ContentItem, MediaAsset, PlatformAccount

    monkeypatch.setattr(media_cache_mod.settings, "data_dir", tmp_path)
    requested: list[str] = []
    from pathlib import Path

    png = Path(__file__).resolve().parent.parent.joinpath("web/public/favicon-32.png").read_bytes()

    async def fake_fetch(url, **_kwargs):
        requested.append(url)
        return egress_mod.ImageResponse(
            url=url, status_code=200, content_type="image/png", content=png
        )

    monkeypatch.setattr(egress_mod, "fetch_image", fake_fetch)
    account = PlatformAccount(platform="wechat_mp", display_name="test")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="wechat_mp",
        external_id="article-index-alignment",
        content_type="article",
        extra={
            "html": (
                '<img src="javascript:alert(1)">'
                '<img src="https://mmbiz.qpic.cn/safe.jpg">'
            )
        },
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id)
    session.add(asset)
    session.commit()

    html = client.get(f"/api/assets/{asset.id}/article").json()["html"]
    soup = BeautifulSoup(html, "html.parser")
    image = soup.find("img")
    assert image is not None
    assert image["src"].startswith(f"/api/articles/{asset.id}/images/0?sig=")
    assert "javascript:" not in html

    response = client.get(image["src"])
    assert response.status_code == 200, response.text
    assert response.content == png
    assert requested == ["https://mmbiz.qpic.cn/safe.jpg"]

    unsigned = client.get(f"/api/articles/{asset.id}/images/0")
    assert unsigned.status_code == 404


def test_article_protocol_relative_image_uses_proxy_as_https(
    client, session, tmp_path, monkeypatch
):
    """WeChat sends ``//img`` URLs; they must be fetched as HTTPS, not app paths."""
    from bs4 import BeautifulSoup

    import app.core.egress as egress_mod
    import app.core.media_cache as media_cache_mod
    from app.models import ContentItem, MediaAsset, PlatformAccount

    monkeypatch.setattr(media_cache_mod.settings, "data_dir", tmp_path)
    requested: list[str] = []
    from pathlib import Path

    png = Path(__file__).resolve().parent.parent.joinpath("web/public/favicon-32.png").read_bytes()

    async def fake_fetch(url, **_kwargs):
        requested.append(url)
        return egress_mod.ImageResponse(
            url=url, status_code=200, content_type="image/png", content=png
        )

    monkeypatch.setattr(egress_mod, "fetch_image", fake_fetch)
    account = PlatformAccount(platform="wechat_mp", display_name="test")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="wechat_mp",
        external_id="article-protocol-relative",
        content_type="article",
        extra={"html": '<img src="//mmbiz.qpic.cn/safe.jpg">'},
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id)
    session.add(asset)
    session.commit()

    html = client.get(f"/api/assets/{asset.id}/article").json()["html"]
    image = BeautifulSoup(html, "html.parser").find("img")
    assert image is not None
    assert image["src"].startswith(f"/api/articles/{asset.id}/images/0?sig=")

    response = client.get(image["src"])
    assert response.status_code == 200, response.text
    assert response.content == png
    assert requested == ["https://mmbiz.qpic.cn/safe.jpg"]


def test_article_proxy_rejects_html_disguised_as_image(client, session, tmp_path, monkeypatch):
    """Remote HTML must not be reflected on our origin through the image proxy."""
    from bs4 import BeautifulSoup

    import app.core.media_cache as media_cache_mod
    from app.models import ContentItem, MediaAsset, PlatformAccount

    monkeypatch.setattr(media_cache_mod.settings, "data_dir", tmp_path)
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"<script>alert(1)</script>"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def send(self, _request):
            return FakeResponse()

    import app.core.egress as egress_mod

    monkeypatch.setattr(egress_mod, "_resolve_public", lambda _u: ("public.example", "93.184.216.34"))
    monkeypatch.setattr(egress_mod, "_PinnedBackend", lambda _address: object())
    monkeypatch.setattr(egress_mod.httpx, "AsyncClient", lambda **_kw: FakeClient())
    account = PlatformAccount(platform="wechat_mp", display_name="test")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="wechat_mp",
        external_id="article-html-disguised",
        content_type="article",
        extra={"html": '<img src="https://public.example/disguised">'},
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id)
    session.add(asset)
    session.commit()

    html = client.get(f"/api/assets/{asset.id}/article").json()["html"]
    image = BeautifulSoup(html, "html.parser").find("img")
    assert image is not None
    response = client.get(image["src"])
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert b"<script>" not in response.content


def test_article_proxy_rejects_cached_html_even_with_png_metadata(client, session, tmp_path, monkeypatch):
    """Legacy cache entries must not bypass the new byte-signature boundary."""
    from bs4 import BeautifulSoup

    import app.core.media_cache as media_cache_mod
    from app.models import ContentItem, MediaAsset, PlatformAccount

    monkeypatch.setattr(media_cache_mod.settings, "data_dir", tmp_path)
    account = PlatformAccount(platform="wechat_mp", display_name="test")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="wechat_mp",
        external_id="article-cached-html",
        content_type="article",
        extra={"html": '<img src="https://mmbiz.qpic.cn/cached.jpg">'},
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id)
    session.add(asset)
    session.commit()

    html = client.get(f"/api/assets/{asset.id}/article").json()["html"]
    image = BeautifulSoup(html, "html.parser").find("img")
    assert image is not None
    source = "https://mmbiz.qpic.cn/cached.jpg"
    key = media_cache_mod._key("article", asset.id, 0, source)
    cache = tmp_path / "media_cache"
    cache.mkdir()
    (cache / f"{key}.json").write_text('{"type":"image/png"}', "utf-8")
    (cache / f"{key}.bin").write_bytes(b"<html><script>alert(1)</script></html>")

    class BrokenResponse:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = b"<html>still-not-an-image</html>"

    class BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def send(self, _request):
            return BrokenResponse()

    import app.core.egress as egress_mod

    monkeypatch.setattr(egress_mod, "_resolve_public", lambda _u: ("mmbiz.qpic.cn", "93.184.216.34"))
    monkeypatch.setattr(egress_mod, "_PinnedBackend", lambda _address: object())
    monkeypatch.setattr(egress_mod.httpx, "AsyncClient", lambda **_kw: BrokenClient())

    response = client.get(image["src"])
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert b"<script>" not in response.content
