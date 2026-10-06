"""贴吧关键词抓取专项测试（mock httpx，不联网）。"""

import hashlib
from urllib.parse import parse_qs, urlparse

import pytest


def _thread(tid="T1", title="露营装备求推荐", name="张三", forum="露营"):
    return {
        "cardInfo": "thread",
        "data": {
            "tid": tid,
            "title": title,
            "content": "正文内容",
            "forum_name": forum,
            "user": {"show_nickname": name, "portrait": "tb.1.abc"},
            "post_num": 12,
            "time": 1703317974,
        },
    }


def _resp(card_list):
    return {"no": 0, "data": {"card_list": card_list}}


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


def test_tieba_sign_matches_algorithm():
    """签名 = 键排序拼 k=v + 密钥，取 MD5；排除 sign/sig/None。"""
    from app.modules.crawler import tieba_sign

    params = {"word": "露营", "pn": 1, "subapp_type": "pc", "sign": "OLD", "junk": None}
    got = tieba_sign.sign(params)
    text = "pn=1subapp_type=pcword=露营" + tieba_sign.PC_SIGN_SECRET
    assert got == hashlib.md5(text.encode("utf-8")).hexdigest()
    # 传入的旧 sign / None 不得影响结果
    assert tieba_sign.sign({**params, "sign": "OTHER"}) == got


@pytest.mark.asyncio
async def test_search_tieba_request_shape_and_normalization(monkeypatch):
    """验证请求形状（host/path/签名参数）、thread 过滤与字段归一化。"""
    from app.modules.crawler import tieba, tieba_sign

    cards = [
        _thread("T1"),
        {"cardInfo": "user", "data": {}},  # 非 thread -> 跳过
        {"cardStyle": "thread", "data": _thread("T2")["data"]},  # cardStyle==thread 保留
    ]
    calls = _install_fake_httpx(monkeypatch, tieba, [_resp(cards), _resp([])])

    items = await tieba.search_tieba("露营", "BDUSS=abc", limit=10, sort="popular")

    assert len(calls) == 2
    parsed = urlparse(calls[0]["url"])
    assert parsed.netloc == "tieba.baidu.com"
    assert parsed.path == "/mo/q/search/multsearch"
    qs = parse_qs(parsed.query, keep_blank_values=True)
    assert qs["word"] == ["露营"]
    assert qs["pn"] == ["1"]
    assert qs["st"] == ["2"]  # popular -> 相关度
    assert qs["subapp_type"] == ["pc"] and qs["_client_type"] == ["20"]
    assert qs["sign"][0]  # 已签名
    assert calls[0]["headers"]["Cookie"] == "BDUSS=abc"

    assert [it["external_id"] for it in items] == ["T1", "T2"]
    t1 = items[0]
    assert t1["content_type"] == "article"
    assert t1["title"] == "露营装备求推荐"
    assert t1["author_name"] == "张三"
    assert t1["url"] == "https://tieba.baidu.com/p/T1"
    assert t1["metrics"]["comment"] == 12
    assert t1["published_at"].startswith("2023-12-23T")


@pytest.mark.asyncio
async def test_search_tieba_pages_until_limit_and_stops_on_empty(monkeypatch):
    from app.modules.crawler import tieba

    responses = [_resp([_thread("T1"), _thread("T2")]), _resp([])]
    calls = _install_fake_httpx(monkeypatch, tieba, responses)
    items = await tieba.search_tieba("x", "B=1", limit=50, sort="latest")
    assert [it["external_id"] for it in items] == ["T1", "T2"]
    assert len(calls) == 2
    qs2 = parse_qs(urlparse(calls[1]["url"]).query, keep_blank_values=True)
    assert qs2["pn"] == ["2"]
    assert qs2["st"] == ["1"]  # latest -> 时间倒序


@pytest.mark.asyncio
async def test_search_tieba_stops_at_limit(monkeypatch):
    from app.modules.crawler import tieba

    calls = _install_fake_httpx(
        monkeypatch, tieba, [_resp([_thread("T0"), _thread("T1"), _thread("T2")])]
    )
    items = await tieba.search_tieba("x", "B=1", limit=2, sort="popular")
    assert [it["external_id"] for it in items] == ["T0", "T1"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_search_tieba_raises_on_error_code(monkeypatch):
    from app.modules.crawler import tieba

    _install_fake_httpx(monkeypatch, tieba, [{"no": 1, "error": "fail", "data": {}}])
    with pytest.raises(RuntimeError, match="贴吧搜索失败"):
        await tieba.search_tieba("x", "B=1", limit=5, sort="popular")


def test_search_tieba_stops_when_page_yields_no_usable_items(monkeypatch):
    """card_list 非空但全部非 thread 时必须停止，避免 pn 死循环。"""
    import asyncio

    from app.modules.crawler import tieba

    calls = {"n": 0}

    class Resp:
        def json(self):
            calls["n"] += 1
            return {"no": 0, "data": {"card_list": [{"cardInfo": "user", "data": {}}]}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return Resp()

    monkeypatch.setattr(tieba.httpx, "AsyncClient", lambda **k: Client())
    items = asyncio.run(tieba.search_tieba("k", "B=1", limit=50, sort="popular"))
    assert items == [] and calls["n"] <= 2


@pytest.mark.asyncio
async def test_crawler_search_dispatches_tieba_native(monkeypatch):
    from app.modules import crawler
    from app.modules.crawler import tieba

    assert "tieba" in crawler._NATIVE
    assert crawler._client._SEARCHERS["tieba"] is tieba.search_tieba

    _install_fake_httpx(monkeypatch, tieba, [_resp([_thread("T1")]), _resp([])])
    items = await crawler.search(platform="tieba", keyword="x", cookie="B=1", limit=1)
    assert [it["external_id"] for it in items] == ["T1"]
