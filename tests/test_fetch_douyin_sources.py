"""抖音新增订阅源类型（点赞/收藏夹/合集/短剧）拉取测试（mock httpx，不联网）。

逐项对应抖小云 VideoTypeEnum：
- like            -> /aweme/v1/web/aweme/favorite
- collect_folder  -> /aweme/v1/web/collects/video/list
- collection      -> /aweme/v1/web/mix/aweme
- series          -> /aweme/v1/web/series/aweme
"""

import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_pace(monkeypatch):
    """跳过抖音翻页随机等待（2~10s）。"""
    from app.modules import douyin
    monkeypatch.setattr(douyin, "_PAGE_DELAY_RANGE", (0.0, 0.0), raising=False)


def _aweme(aweme_id, *, play=10, digg=20, comment=3, collect=4, share=5):
    return {
        "aweme_id": aweme_id,
        "desc": f"作品{aweme_id}",
        "create_time": 1703317974,
        "author": {"nickname": "老王", "sec_uid": "SEC_AUTH"},
        "statistics": {
            "play_count": play, "digg_count": digg,
            "comment_count": comment, "collect_count": collect, "share_count": share,
        },
        "video": {
            "cover": {"url_list": ["https://p3.douyinpic.com/c.jpg"]},
            "play_addr": {"url_list": ["https://www.douyin.com/aweme/v1/play/?video_id=x"]},
        },
    }


class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    @property
    def text(self):
        # 生产代码用 resp.text 识别被 WAF 拦时返回的 "blocked" 字面量。
        if isinstance(self._payload, str):
            return self._payload
        try:
            import json

            return json.dumps(self._payload)
        except Exception:  # noqa: BLE001
            return ""

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


def _patch(monkeypatch, routes):
    """routes: {url_substring: payload 或 callable(params)->payload}。"""
    from app.modules import douyin

    calls: list[dict] = []

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            calls.append({"url": url, "params": params or {}})
            for key, val in routes.items():
                if key in url:
                    payload = val(params or {}) if callable(val) else val
                    return _FakeResp(payload)
            raise AssertionError(f"未预置 URL: {url}")

        async def post(self, url, params=None):
            return await self.get(url, params=params)

    monkeypatch.setattr(douyin, "_client", lambda *a, **k: _C())
    return calls


@pytest.mark.asyncio
async def test_fetch_like_uses_favorite_endpoint(monkeypatch):
    from app.modules import douyin

    payload = {"status_code": 0, "has_more": 0, "cursor": 0, "aweme_list": [_aweme("L1")]}
    calls = _patch(monkeypatch, {"aweme/favorite": payload})
    items = await douyin.DouyinAdapter().fetch_source(
        {"cookie": "c=1", "sec_user_id": "SELF_SEC"}, "like", "SELF_SEC", 10
    )
    assert len(items) == 1
    assert items[0]["external_id"] == "L1"
    assert items[0]["metrics"]["like"] == 20
    assert calls[0]["url"] == "/aweme/v1/web/aweme/favorite"
    assert calls[0]["params"]["sec_user_id"] == "SELF_SEC"
    assert calls[0]["params"]["max_cursor"] == "0"


@pytest.mark.asyncio
async def test_fetch_collect_folder_paginates_and_maps(monkeypatch):
    from app.modules import douyin

    def route(params):
        if params.get("cursor") == "0":
            return {"status_code": 0, "has_more": True, "cursor": 7, "aweme_list": [_aweme("F1")]}
        return {"status_code": 0, "has_more": False, "cursor": 8, "aweme_list": [_aweme("F2")]}

    calls = _patch(monkeypatch, {"collects/video/list": route})
    items = await douyin.DouyinAdapter().fetch_source({"cookie": "c=1"}, "collect_folder", "FOLDER9", 10)
    assert [i["external_id"] for i in items] == ["F1", "F2"]
    assert calls[0]["url"] == "/aweme/v1/web/collects/video/list"
    assert calls[0]["params"]["collects_id"] == "FOLDER9"
    # 第二页用服务端返回的 cursor
    assert calls[1]["params"]["cursor"] == "7"


@pytest.mark.asyncio
async def test_fetch_collection_uses_mix_endpoint(monkeypatch):
    from app.modules import douyin

    payload = {"status_code": 0, "has_more": 0, "cursor": 0, "aweme_list": [_aweme("M1")]}
    calls = _patch(monkeypatch, {"mix/aweme": payload})
    items = await douyin.DouyinAdapter().fetch_source({"cookie": "c=1"}, "collection", "MIX1", 10)
    assert [i["external_id"] for i in items] == ["M1"]
    assert calls[0]["url"] == "/aweme/v1/web/mix/aweme"
    assert calls[0]["params"]["mix_id"] == "MIX1"


@pytest.mark.asyncio
async def test_fetch_series_uses_series_endpoint(monkeypatch):
    from app.modules import douyin

    payload = {"status_code": 0, "has_more": 0, "cursor": 0, "aweme_list": [_aweme("S1")]}
    calls = _patch(monkeypatch, {"series/aweme": payload})
    items = await douyin.DouyinAdapter().fetch_source({"cookie": "c=1"}, "series", "SER1", 10)
    assert [i["external_id"] for i in items] == ["S1"]
    assert calls[0]["url"] == "/aweme/v1/web/series/aweme"
    assert calls[0]["params"]["series_id"] == "SER1"
    assert calls[0]["params"]["pull_type"] == "2"


@pytest.mark.asyncio
async def test_fetch_respects_batch_limit_30(monkeypatch):
    from app.modules import douyin

    async def always_more(cookie, cursor="0", count="20"):
        return {
            "status_code": 0,
            "has_more": True,
            "cursor": int(cursor) + 20,
            "aweme_list": [_aweme(f"X{cursor}_{i}") for i in range(20)],
        }

    monkeypatch.setattr(douyin, "list_collection", always_more)
    items = await douyin.DouyinAdapter().fetch_source({"cookie": "c=1"}, "favorite", "self", 200)
    assert len(items) == 30, "单次同步上限 30（照抄抖小云 IsSyncLimitReached）"


@pytest.mark.asyncio
async def test_fetch_unknown_type_returns_empty(monkeypatch):
    from app.modules import douyin

    def boom(*a, **k):
        raise AssertionError("未知类型不应发起请求")

    monkeypatch.setattr(douyin, "_client", boom)
    items = await douyin.DouyinAdapter().fetch_source({"cookie": "c=1"}, "watch_later", "x", 10)
    assert items == []


@pytest.mark.asyncio
async def test_aweme_to_item_handles_missing_fields():
    from app.modules.douyin import _aweme_to_item

    item = _aweme_to_item({"aweme_id": "1"}, fallback_author_id="FB")
    assert item["author_id"] == "FB"
    assert item["metrics"] == {"play": 0, "like": 0, "comment": 0, "favorite": 0, "share": 0}
    assert item["cover_url"] == ""
    assert item["extra"]["download_url"] == ""
    assert item["published_at"] is None


# ---------------------------------------------------------------------------
# WAF 兜底：纯 HTTP 返回 "blocked" 时必须走浏览器同源 fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_favorite_falls_back_to_browser_when_blocked(monkeypatch):
    """收藏接口纯 HTTP 被 WAF 拦（响应体 "blocked"）时，回退浏览器同源 fetch。"""
    from app.modules import douyin, douyin_browser_fetch

    # 纯 HTTP：POST listcollection 返回 "blocked"
    _patch(monkeypatch, {"listcollection": "blocked"})

    called = {}

    async def fake_fetch_json(path, params, cookie, *, method="GET", referer="https://www.douyin.com/"):
        called.update({"path": path, "method": method, "cookie": cookie})
        return {"status_code": 0, "has_more": 0, "cursor": 0, "aweme_list": [_aweme("FB1")]}

    monkeypatch.setattr(douyin_browser_fetch, "fetch_json", fake_fetch_json)

    items = await douyin.DouyinAdapter().fetch_source({"cookie": "c=cookie"}, "favorite", "", 10)
    assert [i["external_id"] for i in items] == ["FB1"]
    assert called["path"] == "/aweme/v1/web/aweme/listcollection"
    assert called["method"] == "POST"      # 收藏接口必须用 POST，GET 会被平台 404
    assert called["cookie"] == "c=cookie"


@pytest.mark.asyncio
async def test_like_falls_back_to_browser_when_blocked(monkeypatch):
    from app.modules import douyin, douyin_browser_fetch

    _patch(monkeypatch, {"aweme/favorite": "blocked"})

    async def fake_fetch_json(path, params, cookie, *, method="GET", referer="https://www.douyin.com/"):
        return {"status_code": 0, "has_more": 0, "cursor": 0, "aweme_list": [_aweme("LK1")]}

    monkeypatch.setattr(douyin_browser_fetch, "fetch_json", fake_fetch_json)
    items = await douyin.DouyinAdapter().fetch_source(
        {"cookie": "c=1", "sec_user_id": "SELF"}, "like", "SELF", 10
    )
    assert [i["external_id"] for i in items] == ["LK1"]


@pytest.mark.asyncio
async def test_browser_fallback_failure_does_not_crash(monkeypatch):
    """兜底失败（如浏览器不可用）只返回空，不把整批同步打挂。"""
    from app.modules import douyin, douyin_browser_fetch

    _patch(monkeypatch, {"listcollection": "blocked"})

    async def boom(*a, **k):
        raise RuntimeError("浏览器兜底抓取需要 Playwright")

    monkeypatch.setattr(douyin_browser_fetch, "fetch_json", boom)
    items = await douyin.DouyinAdapter().fetch_source({"cookie": "c=1"}, "favorite", "", 10)
    assert items == []
