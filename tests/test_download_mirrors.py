"""抖音直链下载的镜像轮换与节流（照抄抖小云）。

回归背景：
- 抖音 play_addr 直链带 sign= 签名会过期/被 CDN 限流；抖小云重试时会轮换 url_list 里的
  其它镜像并把重试延迟翻倍。此前我们只存/只试第一条，单条失败整条素材就永远失败。
- 批量同步会一次入队几十条下载，此前两个 worker 槽会零间隔连打 CDN，容易触发限流。
"""

import asyncio
from pathlib import Path

import pytest

from app.modules import media_download


def _patch_http(monkeypatch, outcomes):
    """把 httpx.AsyncClient.stream 替换成按 url 返回预置结果的桩。

    outcomes: {url: ("ok", b"data") | ("http", 403)}
    """
    calls: list[str] = []

    class _Resp:
        def __init__(self, status, body):
            self.status_code = status
            self.headers = {"content-type": "video/mp4"}
            self._body = body

        async def aiter_bytes(self, n):
            yield self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url):
            calls.append(url)
            kind = outcomes.get(url)
            if kind is None:
                return _Resp(404, b"")
            if kind[0] == "http":
                return _Resp(kind[1], b"")
            return _Resp(200, kind[1])

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return calls


def test_download_direct_rotates_to_mirror_on_failure(tmp_path, monkeypatch):
    primary = "https://www.douyin.com/aweme/v1/play/?video_id=v1&sign=expired"
    mirror = "https://www.douyin.com/aweme/v1/play/?video_id=v1&sign=fresh"
    calls = _patch_http(monkeypatch, {primary: ("http", 403), mirror: ("ok", b"video-bytes")})

    result = asyncio.run(
        media_download.download_direct(
            primary, tmp_path, cookie="c=1", mirror_urls=[mirror], retries=3
        )
    )

    assert result.ok, result.message
    assert calls == [primary, mirror], "第一条失败后应轮换到镜像"


def test_download_direct_reports_failure_when_all_mirrors_fail(tmp_path, monkeypatch):
    primary = "https://www.douyin.com/aweme/v1/play/?video_id=v2&sign=a"
    mirror = "https://www.douyin.com/aweme/v1/play/?video_id=v2&sign=b"
    _patch_http(monkeypatch, {primary: ("http", 403), mirror: ("http", 403)})

    result = asyncio.run(
        media_download.download_direct(primary, tmp_path, mirror_urls=[mirror], retries=3)
    )
    assert not result.ok
    assert "403" in result.message


def test_download_direct_single_url_no_retry_storm(tmp_path, monkeypatch):
    """只有一个地址时不应重复打（retries 不能超过候选数）。"""
    only = "https://www.douyin.com/aweme/v1/play/?video_id=v3"
    calls = _patch_http(monkeypatch, {only: ("http", 403)})
    result = asyncio.run(media_download.download_direct(only, tmp_path, retries=5))
    assert not result.ok
    assert calls == [only]


def test_download_direct_missing_url_is_clean_error(tmp_path):
    result = asyncio.run(media_download.download_direct("", tmp_path))
    assert not result.ok


@pytest.mark.asyncio
async def test_play_url_list_keeps_all_mirrors():
    from app.modules.douyin import _play_url_list

    aw = {
        "video": {
            "play_addr": {
                "url_list": [
                    "https://cdn.example/b.mp4",                       # 非 douyin 镜像
                    "https://www.douyin.com/aweme/v1/play?v=1&sign=x",  # 首选
                    "https://www.douyin.com/aweme/v1/play?v=1&sign=x",  # 重复
                ]
            }
        }
    }
    urls = _play_url_list(aw)
    assert urls[0] == "https://www.douyin.com/aweme/v1/play?v=1&sign=x"
    assert "https://cdn.example/b.mp4" in urls
    assert len(urls) == 2, "必须去重"


@pytest.mark.asyncio
async def test_download_slot_enforces_min_gap(monkeypatch):
    """连续两次下载之间必须留出最小间隔（风控）。"""
    from app.config import settings
    from app.workers import handlers

    monkeypatch.setattr(settings, "download_min_gap_seconds", 0.3, raising=False)
    monkeypatch.setattr(settings, "download_jitter_seconds", 0.0, raising=False)
    handlers._last_download_at = 0.0

    import time

    t0 = time.monotonic()
    await handlers._await_download_slot()
    await handlers._await_download_slot()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.3, f"两次下载间隔 {elapsed:.2f}s，应 >= 0.3s"
    handlers._last_download_at = 0.0


def test_download_direct_accepts_str_out_dir(tmp_path, monkeypatch):
    """resolve_asset_dir 返回 str；下载器必须能用 str 目录（真机实测崩溃点）。"""
    url = "https://www.douyin.com/aweme/v1/play/?video_id=v9"
    _patch_http(monkeypatch, {url: ("ok", b"bytes")})

    target = tmp_path / "a" / "b"          # 尚不存在
    result = asyncio.run(media_download.download_direct(url, str(target)))
    assert result.ok, result.message
    assert (target / f"{media_download._guess_name(url)}.mp4").exists()


def test_download_direct_rejects_empty_out_dir():
    result = asyncio.run(media_download.download_direct("https://x/y", ""))
    assert not result.ok
    assert "目录" in result.message


def test_cover_proxy_returns_404_without_cover(client, session):
    """没有封面的素材：代理返回 404，而不是 500。"""
    from app.models import ContentItem, MediaAsset, PlatformAccount

    acc = PlatformAccount(platform="douyin", display_name="d")
    session.add(acc)
    session.commit()
    c = ContentItem(account_id=acc.id, platform="douyin", external_id="v1", title="t", url="u")
    session.add(c)
    session.commit()
    a = MediaAsset(content_id=c.id)
    session.add(a)
    session.commit()
    r = client.get(f"/api/assets/{a.id}/cover")
    assert r.status_code == 404


def test_cover_proxy_sends_referer_and_streams_image(client, session, monkeypatch):
    """封面走服务端代理并带 Referer（抖音/B站封面防盗链+签名会过期）。"""
    import httpx
    from app.models import ContentItem, MediaAsset, PlatformAccount

    acc = PlatformAccount(platform="douyin", display_name="d")
    session.add(acc)
    session.commit()
    c = ContentItem(account_id=acc.id, platform="douyin", external_id="v2", title="t",
                    url="u", cover_url="https://p3.douyinpic.com/x.jpeg?sign=1")
    session.add(c)
    session.commit()
    a = MediaAsset(content_id=c.id)
    session.add(a)
    session.commit()

    seen = {}

    class _Resp:
        status_code = 200
        content = b"\xff\xd8\xff\xe0jpegdata"
        headers = {"content-type": "image/jpeg"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen["url"] = url
            seen["headers"] = headers or {}
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    r = client.get(f"/api/assets/{a.id}/cover")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content.startswith(b"\xff\xd8")
    assert "douyin.com" in seen["headers"].get("Referer", ""), "必须补 Referer 绕防盗链"
