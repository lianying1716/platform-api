"""单篇公众号文章导入：URL 校验、解析、入库 API。"""
import asyncio
from datetime import datetime
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "wechat_article.html"


def test_normalize_rejects_non_wechat_host():
    from app.modules.article_import import ArticleImportError, normalize_article_url

    with pytest.raises(ArticleImportError):
        normalize_article_url("https://evil.example.com/s/abc")


def test_normalize_rejects_http_and_non_article_path():
    from app.modules.article_import import ArticleImportError, normalize_article_url

    with pytest.raises(ArticleImportError):
        normalize_article_url("http://mp.weixin.qq.com/s/abc")
    with pytest.raises(ArticleImportError):
        normalize_article_url("https://mp.weixin.qq.com/cgi-bin/home")


def test_normalize_keeps_query_and_strips_fragment():
    from app.modules.article_import import normalize_article_url

    u = normalize_article_url("https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1#wechat_redirect")
    assert u == "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1"


def test_parse_extracts_fields():
    from app.modules.article_import import parse_article_html

    html = FIXTURE.read_text(encoding="utf-8")
    out = parse_article_html(html, "https://mp.weixin.qq.com/s/AbCdEf123456")
    assert out["title"] == "测试文章标题"
    assert out["author_name"] == "测试公众号"
    assert out["cover_url"] == "https://mmbiz.qpic.cn/cover.jpg"
    assert "第一段正文" in out["text"]
    assert "第二段正文" in out["text"]
    assert out["external_id"]
    assert out["published_at"] is not None
    assert "js_content" in out["html"]


def test_parse_sanitizes_dangerous_article_html():
    from app.modules.article_import import parse_article_html

    html = FIXTURE.read_text(encoding="utf-8").replace(
        '<p>第一段正文。</p>',
        '<p onclick="steal()">第一段正文。</p>'
        '<script>alert(1)</script>'
        '<img src="x" onerror="steal()">'
        '<a href="javascript:alert(1)">bad</a>',
    )
    out = parse_article_html(html, "https://mp.weixin.qq.com/s/AbCdEf123456")
    lowered = out["html"].lower()
    assert "<script" not in lowered
    assert "onclick" not in lowered
    assert "onerror" not in lowered
    assert "javascript:" not in lowered
    assert "第一段正文" in out["html"]


def test_parse_falls_back_title_to_og_when_activity_name_missing():
    from app.modules.article_import import parse_article_html

    html = FIXTURE.read_text(encoding="utf-8").replace('id="activity-name"', 'id="x"')
    out = parse_article_html(html, "https://mp.weixin.qq.com/s/AbCdEf123456")
    assert out["title"] == "测试文章标题"


def test_parse_normalizes_protocol_relative_images_and_links():
    from app.modules.article_import import parse_article_html

    html = FIXTURE.read_text(encoding="utf-8").replace(
        '<p>第一段正文。</p>',
        '<p>第一段正文。</p><img src="//mmbiz.qpic.cn/a.jpg">'
        '<a href="//mp.weixin.qq.com/s/x">link</a>',
    )
    out = parse_article_html(html, "https://mp.weixin.qq.com/s/AbCdEf123456")
    assert 'src="https://mmbiz.qpic.cn/a.jpg"' in out["html"]
    assert 'href="https://mp.weixin.qq.com/s/x"' in out["html"]


def test_parse_rejects_backslash_disguised_external_link():
    """Browsers normalize /\\evil.example to //evil.example; do not keep it as same-origin."""
    from app.modules.article_import import parse_article_html

    html = FIXTURE.read_text(encoding="utf-8").replace(
        '<p>第一段正文。</p>',
        '<p>第一段正文。</p><a href="/\\evil.example/steal">bad</a>'
        '<img src="/\\evil.example/pixel.png">',
    )
    out = parse_article_html(html, "https://mp.weixin.qq.com/s/AbCdEf123456")
    assert "evil.example" not in out["html"]
    assert "<a" in out["html"] and "<img" in out["html"]


def test_import_endpoint_creates_asset_and_transcript(client, monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")

    async def fake_fetch(url, cookie=""):
        from app.modules.article_import import parse_article_html

        return parse_article_html(html, "https://mp.weixin.qq.com/s/AbCdEf123456")

    monkeypatch.setattr("app.services.import_service.article_import.fetch_article", fake_fetch)

    r = client.post(
        "/api/imports/wechat-article",
        json={"url": "https://mp.weixin.qq.com/s/AbCdEf123456"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "测试文章标题"
    assert body["already_existed"] is False

    detail = client.get(f"/api/assets/{body['asset_id']}").json()
    assert detail["content"]["platform"] == "wechat_mp"
    assert "第一段正文" in detail["transcript"]["full_text"]


def test_import_endpoint_is_idempotent(client, monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")

    async def fake_fetch(url, cookie=""):
        from app.modules.article_import import parse_article_html

        return parse_article_html(html, "https://mp.weixin.qq.com/s/AbCdEf123456")

    monkeypatch.setattr("app.services.import_service.article_import.fetch_article", fake_fetch)
    url = "https://mp.weixin.qq.com/s/AbCdEf123456"
    first = client.post("/api/imports/wechat-article", json={"url": url}).json()
    second = client.post("/api/imports/wechat-article", json={"url": url}).json()
    assert second["already_existed"] is True
    assert second["asset_id"] == first["asset_id"]


def test_import_endpoint_rejects_bad_url(client, monkeypatch):
    from app.modules import article_import as real_mod

    real = real_mod.fetch_article  # capture BEFORE patching

    async def fake_fetch(url, cookie=""):
        return await real(url, cookie=cookie)

    monkeypatch.setattr(real_mod, "fetch_article", fake_fetch)
    r = client.post("/api/imports/wechat-article", json={"url": "https://evil.example.com/s/x"})
    assert r.status_code == 422
    assert "微信" in r.text


def test_fetch_rejects_redirect_without_following(monkeypatch):
    """安全回归：302 必须被拒绝，且不跟随跳转（只发一次请求）。"""
    import httpx

    from app.modules.article_import import ArticleImportError, fetch_article

    calls = []

    async def fake_get(self, url, **kwargs):
        calls.append(str(url))
        return httpx.Response(
            302,
            headers={"location": "https://evil.example.com/steal"},
            request=httpx.Request("GET", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(ArticleImportError):
        asyncio.run(fetch_article("https://mp.weixin.qq.com/s/AbCdEf123456"))
    assert len(calls) == 1


def test_fetch_wraps_non_2xx_as_article_import_error(monkeypatch):
    """4xx/5xx 归一为 ArticleImportError，端点才能返回 422 而不是 500。"""
    import httpx

    from app.modules.article_import import ArticleImportError, fetch_article

    async def fake_get(self, url, **kwargs):
        return httpx.Response(404, request=httpx.Request("GET", str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(ArticleImportError):
        asyncio.run(fetch_article("https://mp.weixin.qq.com/s/AbCdEf123456"))


def test_fetch_wraps_network_error_as_article_import_error(monkeypatch):
    """超时/连接错误同样归一，避免冒泡成 500。"""
    import httpx

    from app.modules.article_import import ArticleImportError, fetch_article

    async def fake_get(self, url, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(ArticleImportError):
        asyncio.run(fetch_article("https://mp.weixin.qq.com/s/AbCdEf123456"))


def test_parse_time_normalizes_iso_offset_to_utc():
    """ISO published_at 带时区偏移时应换算为 UTC 再丢弃 tzinfo。"""
    from bs4 import BeautifulSoup

    from app.modules.article_import import _parse_time

    html = '<meta property="article:published_time" content="2026-06-01T10:00:00+08:00">'
    soup = BeautifulSoup(html, "html.parser")
    assert _parse_time(html, soup) == datetime(2026, 6, 1, 2, 0, 0)
