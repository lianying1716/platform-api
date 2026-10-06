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


def _patch_sign(monkeypatch, module):
    """替换 module.zhihu_sign.sign，记录调用参数并返回固定签名头。"""
    calls: dict = {}

    def fake_sign(url, cookies):
        calls["url"] = url
        calls["cookies"] = cookies
        return {"x-zst-81": "ZST81", "x-zse-96": "ZSE96"}

    monkeypatch.setattr(module.zhihu_sign, "sign", fake_sign)
    return calls


@pytest.mark.asyncio
async def test_search_zhihu_request_shape_and_normalization(monkeypatch):
    """验证请求形状（host/path/params/签名头）、签名覆盖完整 URL，以及混合响应归一化。"""
    from app.modules.crawler import zhihu

    sign_calls = _patch_sign(monkeypatch, zhihu)
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
        # 未知类型 -> 跳过
        {"type": "search_result", "object": {"type": "people", "id": "p1"}},
        # 缺 id -> 跳过
        {"type": "answer", "object": {"title": "no id"}},
    ]
    calls = _install_fake_httpx(monkeypatch, zhihu, [_resp(mixed, is_end=True)])

    items = await zhihu.search_zhihu("露营", "d_c0=abc;z_c0=def", limit=10, sort="popular")

    # 请求形状
    assert len(calls) == 1
    parsed = urlparse(calls[0]["url"])
    assert parsed.netloc == "www.zhihu.com"
    assert parsed.path == "/api/v4/search_v3"
    qs = parse_qs(parsed.query, keep_blank_values=True)
    assert qs["q"] == ["露营"]
    assert qs["offset"] == ["0"]
    assert qs["sort"] == [""]  # popular -> 空
    assert qs["limit"] == ["20"]
    assert qs["t"] == ["general"] and qs["search_source"] == ["Filter"]

    # 签名器收到含查询串的完整 URL
    assert sign_calls["url"] == calls[0]["url"]
    assert "?" in sign_calls["url"] and "q=" in sign_calls["url"]
    assert sign_calls["cookies"] == "d_c0=abc;z_c0=def"

    # 签名头与 Cookie/UA/Referer
    headers = calls[0]["headers"]
    assert headers["x-zst-81"] == "ZST81"
    assert headers["x-zse-96"] == "ZSE96"
    assert headers["Cookie"] == "d_c0=abc;z_c0=def"
    assert headers["User-Agent"] and "Referer" in headers

    # 归一化：answer + article + zvideo，未知/缺 id 被跳过
    assert [it["content_type"] for it in items] == ["answer", "article", "video"]

    a = items[0]
    assert a["external_id"] == "a1"
    assert a["title"] == "露营装备清单"  # <em> 标签已清除
    assert a["author_name"] == "张三"
    assert a["author_id"] == "u1"
    assert a["url"] == "https://www.zhihu.com/question/q1/answer/a1"
    assert a["metrics"] == {"like": 42, "comment": 7, "play": 0}

    art = items[1]
    assert art["external_id"] == "art1"
    assert art["url"] == "https://zhuanlan.zhihu.com/p/art1"
    assert art["cover_url"] == "https://pic.zhihu.com/cover.jpg"
    assert art["metrics"]["like"] == 10 and art["metrics"]["comment"] == 2

    z = items[2]
    assert z["external_id"] == "z1"
    assert z["url"] == "https://www.zhihu.com/zvideo/z1"
    assert z["cover_url"] == "https://pic.zhihu.com/z.jpg"
    assert z["metrics"] == {"like": 5, "comment": 1, "play": 999}


@pytest.mark.asyncio
async def test_search_zhihu_paging_stops_on_is_end(monkeypatch):
    """paging.is_end 为真时停止翻页；第二页 offset/lc_idx 按页大小前进。"""
    from app.modules.crawler import zhihu

    _patch_sign(monkeypatch, zhihu)
    responses = [_resp([_answer("a1")], is_end=False), _resp([_answer("a2")], is_end=True)]
    calls = _install_fake_httpx(monkeypatch, zhihu, responses)

    items = await zhihu.search_zhihu("x", "d_c0=1", limit=50, sort="popular")
    assert [it["external_id"] for it in items] == ["a1", "a2"]
    assert len(calls) == 2
    qs2 = parse_qs(urlparse(calls[1]["url"]).query, keep_blank_values=True)
    assert qs2["offset"] == ["20"]
    assert qs2["lc_idx"] == ["20"]


@pytest.mark.asyncio
async def test_search_zhihu_stops_at_limit(monkeypatch):
    """到 limit 即停，不再请求下一页。"""
    from app.modules.crawler import zhihu

    _patch_sign(monkeypatch, zhihu)
    responses = [_resp([_answer("a0"), _answer("a1"), _answer("a2")], is_end=False)]
    calls = _install_fake_httpx(monkeypatch, zhihu, responses)

    items = await zhihu.search_zhihu("x", "d_c0=1", limit=2, sort="popular")
    assert [it["external_id"] for it in items] == ["a0", "a1"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_search_zhihu_latest_sort(monkeypatch):
    """sort=latest 映射为 created_time。"""
    from app.modules.crawler import zhihu

    _patch_sign(monkeypatch, zhihu)
    calls = _install_fake_httpx(monkeypatch, zhihu, [_resp([_answer("a1")], is_end=True)])

    await zhihu.search_zhihu("x", "d_c0=1", limit=5, sort="latest")
    qs = parse_qs(urlparse(calls[0]["url"]).query, keep_blank_values=True)
    assert qs["sort"] == ["created_time"]


@pytest.mark.asyncio
async def test_crawler_search_dispatches_zhihu_native(monkeypatch):
    """统一入口 crawler.search 对 zhihu 走原生纯 Python 路径（非子进程兜底）。"""
    from app.modules import crawler
    from app.modules.crawler import zhihu

    assert "zhihu" in crawler._NATIVE
    assert crawler._client._SEARCHERS["zhihu"] is zhihu.search_zhihu

    _patch_sign(monkeypatch, zhihu)
    _install_fake_httpx(monkeypatch, zhihu, [_resp([_answer("a1")], is_end=True)])

    items = await crawler.search(platform="zhihu", keyword="x", cookie="d_c0=1", limit=1)
    assert [it["external_id"] for it in items] == ["a1"]
