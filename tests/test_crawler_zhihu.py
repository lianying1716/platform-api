"""知乎关键词抓取专项测试（mock httpx + monkeypatch 签名器，不联网/不跑 Node）。"""

from urllib.parse import parse_qs, urlparse

import pytest


def _answer(aid="a1", title="露营<em>装备</em>清单", uid="u1", name="张三"):
    return {
        "type": "answer",
        "object": {
            "id": aid, "title": title, "question": {"id": "q1"},
            "author": {"id": uid, "name": name, "url_token": "zs"},
            "voteup_count": 42, "comment_count": 7,
        },
    }


def _resp(items, is_end=False):
    return {"data": items, "paging": {"is_end": is_end}}


def _install_fake_httpx(monkeypatch, module, responses):
    """把 module 内 httpx.AsyncClient 换成记录 url/headers 的假客户端，按序返回 canned 响应。"""
    calls: list[dict] = []
    queue = list(responses)

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **kw):
            calls.append({"url": url, "headers": headers or {}})
            return FakeResponse(queue.pop(0))

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    return calls


@pytest.mark.asyncio
async def test_search_zhihu_uses_browser_self_signed_response(monkeypatch):
    """知乎搜索不再本地签名：由受管浏览器监听平台自身的 search_v3 响应。"""
    from app.modules.crawler import zhihu
    from app.modules import browser_fetch

    mixed = [
        _answer("a1"),
        {"type": "search_result", "object": {
            "type": "article", "id": "art1", "title": "我的露营日记",
            "author": {"id": "u2", "name": "李四"}, "voteup_count": 10, "comment_count": 2,
            "image_url": "https://pic.zhihu.com/cover.jpg",
        }},
        {"type": "zvideo", "object": {
            "id": "z1", "title": "露营 vlog", "author": {"id": "u3", "name": "王五"},
            "voteup_count": 5, "comment_count": 1,
            "video": {"thumbnail": "https://pic.zhihu.com/z.jpg", "play_count": 999},
        }},
        {"type": "search_result", "object": {"type": "people", "id": "p1"}},
        {"type": "answer", "object": {"title": "no id"}},
    ]
    calls = {}

    async def fake_fetch_search(*, search_url, api_token, cookie, domain, pages=1, timeout_ms=20_000):
        calls.update({"search_url": search_url, "api_token": api_token, "cookie": cookie,
                      "domain": domain, "pages": pages})
        return [_resp(mixed, is_end=True)]

    monkeypatch.setattr(browser_fetch, "fetch_search", fake_fetch_search)

    items = await zhihu.search_zhihu("露营", "d_c0=abc;z_c0=def", limit=10, sort="popular")

    assert calls["api_token"] == "/api/v4/search_v3"
    assert calls["cookie"] == "d_c0=abc;z_c0=def"
    assert calls["domain"] == ".zhihu.com"
    assert "q=%E9%9C%B2%E8%90%A5" in calls["search_url"]
    assert [it["content_type"] for it in items] == ["answer", "article", "video"]

    a = items[0]
    assert a["external_id"] == "a1"
    assert a["title"] == "露营装备清单"
    assert a["author_name"] == "张三"
    assert a["author_id"] == "u1"
    assert a["url"] == "https://www.zhihu.com/question/q1/answer/a1"
    assert a["metrics"] == {"like": 42, "comment": 7, "play": 0}

    art = items[1]
    assert art["external_id"] == "art1"
    assert art["url"] == "https://zhuanlan.zhihu.com/p/art1"
    assert art["cover_url"] == "https://pic.zhihu.com/cover.jpg"

    z = items[2]
    assert z["external_id"] == "z1"
    assert z["url"] == "https://www.zhihu.com/zvideo/z1"
    assert z["cover_url"] == "https://pic.zhihu.com/z.jpg"
    assert z["metrics"] == {"like": 5, "comment": 1, "play": 999}


@pytest.mark.asyncio
async def test_search_zhihu_stops_at_limit(monkeypatch):
    """到 limit 即停，不再消费后续响应。"""
    from app.modules.crawler import zhihu
    from app.modules import browser_fetch

    async def fake_fetch_search(**kwargs):
        return [_resp([_answer("a0"), _answer("a1"), _answer("a2")], is_end=False)]

    monkeypatch.setattr(browser_fetch, "fetch_search", fake_fetch_search)
    items = await zhihu.search_zhihu("x", "d_c0=1", limit=2, sort="popular")
    assert [it["external_id"] for it in items] == ["a0", "a1"]


@pytest.mark.asyncio
async def test_crawler_search_dispatches_zhihu_native(monkeypatch):
    from app.modules import crawler
    from app.modules.crawler import zhihu
    from app.modules import browser_fetch

    assert "zhihu" in crawler._NATIVE
    assert crawler._client._SEARCHERS["zhihu"] is zhihu.search_zhihu

    async def fake_fetch_search(**kwargs):
        return [_resp([_answer("a1")], is_end=True)]

    monkeypatch.setattr(browser_fetch, "fetch_search", fake_fetch_search)
    items = await crawler.search(platform="zhihu", keyword="x", cookie="d_c0=1", limit=1)
    assert [it["external_id"] for it in items] == ["a1"]


def test_search_zhihu_normalizer_skips_unknown_and_missing_id():
    """归一化层必须过滤未知类型与缺 id 的条目。"""
    from app.modules.crawler import zhihu

    rows = [{"type": "unknown", "object": {}}, {"type": "answer", "object": {"title": "no id"}}]
    assert [zhihu._normalize_item(r) for r in rows] == [None, None]
