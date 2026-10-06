"""微博关键词抓取专项测试（mock httpx，不联网）。"""

from urllib.parse import parse_qs, urlparse

import pytest


def _mblog(mid="M1", text="露营<em>装备</em>清单", name="张三", uid="100"):
    return {
        "id": mid,
        "text": text,
        "user": {"id": uid, "screen_name": name},
        "attitudes_count": 11,
        "comments_count": 3,
        "reposts_count": 2,
        "pics": [{"url": "https://wx/pic.jpg"}],
        "created_at": "Sat Dec 23 17:12:54 +0800 2023",
    }


def _card(mblog, card_type=9):
    return {"card_type": card_type, "mblog": mblog}


def _resp(cards):
    return {"ok": 1, "data": {"cards": cards}}


def _install_fake_httpx(monkeypatch, module, responses):
    """把 module 内 httpx.AsyncClient 换成记录 url 的假客户端，按序返回 canned 响应。"""
    calls: list[dict] = []
    queue = list(responses)

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *a, headers=None, **k):
            self._headers = dict(headers or {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **kw):
            merged = {**self._headers, **(headers or {})}
            calls.append({"url": url, "headers": merged})
            return FakeResponse(queue.pop(0))

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    return calls


@pytest.mark.asyncio
async def test_search_weibo_request_shape_and_normalization(monkeypatch):
    """验证请求形状（host/path/containerid）、card_type==9 过滤与字段归一化。"""
    from app.modules.crawler import weibo

    cards = [
        _card(_mblog("M1")),
        # card_type != 9 -> 跳过；但 card_group 内嵌的 type 9 要保留
        {"card_type": 11, "card_group": [_card(_mblog("M2", text="内嵌"))]},
        _card(_mblog("M3", text="视频贴"), card_type=9) | {},
    ]
    cards[2]["mblog"]["page_info"] = {
        "object_type": "video", "page_pic": {"url": "https://wx/vthumb.jpg"},
    }
    calls = _install_fake_httpx(monkeypatch, weibo, [_resp(cards), _resp([])])

    items = await weibo.search_weibo("露营", "SUB=abc", limit=10, sort="popular")

    assert len(calls) == 2
    parsed = urlparse(calls[0]["url"])
    assert parsed.netloc == "m.weibo.cn"
    assert parsed.path == "/api/container/getIndex"
    qs = parse_qs(parsed.query, keep_blank_values=True)
    assert qs["containerid"] == ["100103type=60&q=露营"]
    assert qs["page_type"] == ["searchall"]
    assert qs["page"] == ["1"]
    assert calls[0]["headers"]["Cookie"] == "SUB=abc"

    # card_type==9 与内嵌 card_group 各取一条；M3 视频
    assert [it["external_id"] for it in items] == ["M1", "M2", "M3"]

    m1 = items[0]
    assert m1["content_type"] == "image"  # 有 pics
    assert m1["title"] == "露营装备清单"  # HTML 标签已清除
    assert m1["author_name"] == "张三"
    assert m1["author_id"] == "100"
    assert m1["url"] == "https://m.weibo.cn/detail/M1"
    assert m1["cover_url"] == "https://wx/pic.jpg"
    assert m1["metrics"] == {"like": 11, "comment": 3, "share": 2, "play": 0}
    assert m1["published_at"].startswith("2023-12-23T09:12:54")  # +0800 -> UTC

    m3 = items[2]
    assert m3["content_type"] == "video"
    assert m3["cover_url"] == "https://wx/vthumb.jpg"


@pytest.mark.asyncio
async def test_search_weibo_pages_until_limit_and_stops_on_empty(monkeypatch):
    """到 limit 即停；cards 为空时停止翻页。"""
    from app.modules.crawler import weibo

    responses = [
        _resp([_card(_mblog("M1")), _card(_mblog("M2"))]),
        _resp([]),
    ]
    calls = _install_fake_httpx(monkeypatch, weibo, responses)
    items = await weibo.search_weibo("x", "S=1", limit=50, sort="latest")
    assert [it["external_id"] for it in items] == ["M1", "M2"]
    assert len(calls) == 2
    qs2 = parse_qs(urlparse(calls[1]["url"]).query, keep_blank_values=True)
    assert qs2["page"] == ["2"]
    assert qs2["containerid"] == ["100103type=61&q=x"]  # latest -> 61


@pytest.mark.asyncio
async def test_search_weibo_stops_at_limit(monkeypatch):
    from app.modules.crawler import weibo

    calls = _install_fake_httpx(
        monkeypatch, weibo, [_resp([_card(_mblog("M0")), _card(_mblog("M1")), _card(_mblog("M2"))])]
    )
    items = await weibo.search_weibo("x", "S=1", limit=2, sort="popular")
    assert [it["external_id"] for it in items] == ["M0", "M1"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_search_weibo_raises_on_not_ok(monkeypatch):
    """ok!=1（Cookie 失效 / 风控）抛可读异常。"""
    from app.modules.crawler import weibo

    _install_fake_httpx(monkeypatch, weibo, [{"ok": 0, "msg": "登录", "data": {}}])
    with pytest.raises(RuntimeError, match="微博搜索失败"):
        await weibo.search_weibo("x", "S=1", limit=5, sort="popular")


def test_search_weibo_stops_when_page_yields_no_usable_items(monkeypatch):
    """cards 非空但全部被过滤（非 type 9）时必须停止，避免 offset 死循环。"""
    import asyncio

    from app.modules.crawler import weibo

    calls = {"n": 0}

    class Resp:
        def json(self):
            calls["n"] += 1
            return {"ok": 1, "data": {"cards": [{"card_type": 11, "mblog": {}}]}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return Resp()

    monkeypatch.setattr(weibo.httpx, "AsyncClient", lambda **k: Client())
    items = asyncio.run(weibo.search_weibo("k", "S=1", limit=50, sort="popular"))
    assert items == [] and calls["n"] <= 2


@pytest.mark.asyncio
async def test_crawler_search_dispatches_weibo_native(monkeypatch):
    """统一入口 crawler.search 对 weibo 走原生纯 Python 路径。"""
    from app.modules import crawler
    from app.modules.crawler import weibo

    assert "weibo" in crawler._NATIVE
    assert crawler._client._SEARCHERS["weibo"] is weibo.search_weibo

    _install_fake_httpx(monkeypatch, weibo, [_resp([_card(_mblog("M1"))]), _resp([])])
    items = await crawler.search(platform="weibo", keyword="x", cookie="S=1", limit=1)
    assert [it["external_id"] for it in items] == ["M1"]
