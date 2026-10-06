"""快手关键词抓取专项测试（mock httpx，不联网）。"""

import pytest


def _feed(pid, caption="标题", name="作者", aid="100"):
    return {
        "author": {"id": aid, "name": name, "headerUrl": "https://a/avatar.jpg"},
        "photo": {
            "id": pid, "caption": caption, "likeCount": 11, "viewCount": 22,
            "commentCount": 3, "coverUrl": "https://c/cover.jpg",
            "timestamp": 1, "duration": 2,
        },
    }


def _install_fake_httpx(monkeypatch, module, responses):
    """把 module 内 httpx.AsyncClient 换成记录调用的假客户端，按序返回 canned 响应。"""
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

        async def post(self, url, json=None, **kw):
            calls.append({"url": url, "json": json})
            return FakeResponse(queue.pop(0))

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    return calls


@pytest.mark.asyncio
async def test_search_kuaishou_normalizes_and_pages(monkeypatch):
    """验证请求形状（graphql 端点/operationName/query）、归一化字段与按 limit 分页停止。"""
    from app.modules.crawler import kuaishou

    monkeypatch.setattr(kuaishou, "_PAGE_SLEEP", 0)
    responses = [
        {"data": {"visionSearchPhoto": {
            "result": 1, "feeds": [_feed("v1"), _feed("v2")],
            "pcursor": "2", "searchSessionId": "sess-1"}}},
        {"data": {"visionSearchPhoto": {
            "result": 1, "feeds": [_feed("v3"), _feed("v4")],
            "pcursor": "no_more", "searchSessionId": "sess-1"}}},
    ]
    calls = _install_fake_httpx(monkeypatch, kuaishou, responses)

    items = await kuaishou.search_kuaishou("露营", "did=web_abc", limit=3, sort="popular")

    # 到 limit 即停：第一页 2 条 + 第二页补 1 条，共请求 2 页
    assert len(items) == 3
    assert len(calls) == 2
    assert [it["external_id"] for it in items] == ["v1", "v2", "v3"]

    # 请求形状
    assert calls[0]["url"] == "https://www.kuaishou.com/graphql"
    body = calls[0]["json"]
    assert body["operationName"] == "visionSearchPhoto"
    assert body["variables"]["keyword"] == "露营"
    assert body["variables"]["page"] == "search"
    assert body["query"] and "visionSearchPhoto" in body["query"]  # query 非空
    # pcursor 翻页 + searchSessionId 跨页携带
    assert calls[1]["json"]["variables"]["pcursor"] == "2"
    assert calls[1]["json"]["variables"]["searchSessionId"] == "sess-1"

    # 归一化字段
    it = items[0]
    assert it["external_id"] == "v1"
    assert it["content_type"] == "video"
    assert it["title"] == "标题"
    assert it["author_name"] == "作者"
    assert it["author_id"] == "100"
    assert it["url"] == "https://www.kuaishou.com/short-video/v1"
    assert it["cover_url"] == "https://c/cover.jpg"
    assert it["metrics"] == {"play": 22, "like": 11, "comment": 3}


@pytest.mark.asyncio
async def test_search_kuaishou_stops_on_no_more(monkeypatch):
    """pcursor 为 no_more 时停止翻页，不足 limit 也正常返回。"""
    from app.modules.crawler import kuaishou

    monkeypatch.setattr(kuaishou, "_PAGE_SLEEP", 0)
    responses = [
        {"data": {"visionSearchPhoto": {
            "result": 1, "feeds": [_feed("v1")],
            "pcursor": "no_more", "searchSessionId": "s"}}},
    ]
    calls = _install_fake_httpx(monkeypatch, kuaishou, responses)

    items = await kuaishou.search_kuaishou("x", "c", limit=10, sort="popular")
    assert [it["external_id"] for it in items] == ["v1"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_search_kuaishou_result_not_1_raises(monkeypatch):
    """result != 1 抛可读 RuntimeError。"""
    from app.modules.crawler import kuaishou

    monkeypatch.setattr(kuaishou, "_PAGE_SLEEP", 0)
    responses = [
        {"data": {"visionSearchPhoto": {"result": 2, "feeds": [], "pcursor": "no_more"}}},
    ]
    _install_fake_httpx(monkeypatch, kuaishou, responses)

    with pytest.raises(RuntimeError, match="快手搜索失败"):
        await kuaishou.search_kuaishou("x", "c", limit=5, sort="popular")


@pytest.mark.asyncio
async def test_search_kuaishou_missing_feeds_raises(monkeypatch):
    """缺失 feeds 字段抛可读 RuntimeError。"""
    from app.modules.crawler import kuaishou

    monkeypatch.setattr(kuaishou, "_PAGE_SLEEP", 0)
    responses = [{"data": {"visionSearchPhoto": {"result": 1, "pcursor": "no_more"}}}]
    _install_fake_httpx(monkeypatch, kuaishou, responses)

    with pytest.raises(RuntimeError, match="快手搜索失败"):
        await kuaishou.search_kuaishou("x", "c", limit=5, sort="popular")


@pytest.mark.asyncio
async def test_crawler_search_dispatches_kuaishou_native(monkeypatch):
    """统一入口 crawler.search 对 kuaishou 走原生纯 Python 路径（非子进程兜底）。"""
    from app.modules import crawler
    from app.modules.crawler import kuaishou

    assert "kuaishou" in crawler._NATIVE
    assert crawler._client._SEARCHERS["kuaishou"] is kuaishou.search_kuaishou

    monkeypatch.setattr(kuaishou, "_PAGE_SLEEP", 0)
    responses = [
        {"data": {"visionSearchPhoto": {
            "result": 1, "feeds": [_feed("v1")], "pcursor": "no_more", "searchSessionId": "s"}}},
    ]
    _install_fake_httpx(monkeypatch, kuaishou, responses)

    items = await crawler.search(platform="kuaishou", keyword="x", cookie="c", limit=1)
    assert [it["external_id"] for it in items] == ["v1"]
