"""远端来源选择器专项测试（Task 3 / 设计 §A3）。

覆盖 B站 / 抖音适配器的 ``list_sources(credential, source_type)``：
- 各支持的 source_type 返回 ``[{remote_id, title}]`` 形状正确；
- 缺少必需凭证字段（B站 mid / 抖音 sec_user_id）或类型不支持 → 返回 ``[]``；
- 网络/解析异常不抛，降级为空列表。

全程 mock httpx（不发真实请求）。
"""

import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_pace(monkeypatch):
    """测试里把抖音翻页随机等待调成 0。"""
    from app.modules import douyin
    monkeypatch.setattr(douyin, "_PAGE_DELAY_RANGE", (0.0, 0.0), raising=False)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """按 URL 关键字返回预置响应，并记录调用参数。"""

    def __init__(self, routes, calls):
        self._routes = routes
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, cookies=None):
        self._calls.append({"url": url, "params": params or {}})
        for key, payload in self._routes.items():
            if key in url:
                if isinstance(payload, Exception):
                    raise payload
                return _FakeResp(payload)
        raise AssertionError(f"未预置的 URL: {url}")


def _patch_client(monkeypatch, module, routes):
    calls: list[dict] = []

    def factory(*args, **kwargs):
        return _FakeClient(routes, calls)

    monkeypatch.setattr(module, "_client", factory)
    return calls


def _bili_credential(mid="12345"):
    return {"sessdata": "s", "bili_jct": "j", "dedeuserid": mid}


# ---------------------------------------------------------------- B站


@pytest.mark.asyncio
async def test_bilibili_favorite(monkeypatch):
    from app.modules import bilibili

    calls = _patch_client(
        monkeypatch,
        bilibili,
        {"folder/created/list-all": {"code": 0, "data": {"list": [{"id": 111, "title": "默认收藏夹"}]}}},
    )
    out = await bilibili.BilibiliAdapter().list_sources(_bili_credential(), "favorite")
    assert out == [{"remote_id": "111", "title": "默认收藏夹"}]
    assert calls[0]["params"]["up_mid"] == "12345"


@pytest.mark.asyncio
async def test_bilibili_collection(monkeypatch):
    from app.modules import bilibili

    _patch_client(
        monkeypatch,
        bilibili,
        {
            "seasons_series_list": {
                "code": 0,
                "data": {
                    "items_lists": {
                        "seasons_list": [{"meta": {"season_id": 9001, "name": "合集A"}}],
                        "series_list": [{"meta": {"series_id": 7001, "name": "系列B"}}],
                    }
                },
            }
        },
    )
    out = await bilibili.BilibiliAdapter().list_sources(_bili_credential(), "collection")
    assert out == [
        {"remote_id": "9001", "title": "合集A"},
        {"remote_id": "7001", "title": "系列B"},
    ]


@pytest.mark.asyncio
async def test_bilibili_creator(monkeypatch):
    from app.modules import bilibili

    calls = _patch_client(
        monkeypatch,
        bilibili,
        {"relation/followings": {"code": 0, "data": {"list": [{"mid": 42, "uname": "UP主A"}]}}},
    )
    out = await bilibili.BilibiliAdapter().list_sources(_bili_credential(), "creator")
    assert out == [{"remote_id": "42", "title": "UP主A"}]
    assert calls[0]["params"]["mid"] == "12345"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["favorite", "collection", "creator"])
async def test_bilibili_missing_mid_returns_empty(monkeypatch, source_type):
    from app.modules import bilibili

    def boom(*args, **kwargs):  # 缺少 mid 时不应发起请求
        raise AssertionError("缺少 mid 时不应发起请求")

    monkeypatch.setattr(bilibili, "_client", boom)
    out = await bilibili.BilibiliAdapter().list_sources({"sessdata": "s"}, source_type)
    assert out == []


@pytest.mark.asyncio
async def test_bilibili_unsupported_type_returns_empty(monkeypatch):
    from app.modules import bilibili

    def boom(*args, **kwargs):
        raise AssertionError("不支持的类型不应发起请求")

    monkeypatch.setattr(bilibili, "_client", boom)
    out = await bilibili.BilibiliAdapter().list_sources(_bili_credential(), "watch_later")
    assert out == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["favorite", "collection", "creator"])
async def test_bilibili_http_error_does_not_raise(monkeypatch, source_type):
    from app.modules import bilibili

    routes = {
        key: httpx.ConnectError("boom")
        for key in ("folder/created", "seasons_series_list", "relation/followings")
    }
    _patch_client(monkeypatch, bilibili, routes)
    out = await bilibili.BilibiliAdapter().list_sources(_bili_credential(), source_type)
    assert out == []


# ---------------------------------------------------------------- 抖音


@pytest.mark.asyncio
async def test_douyin_favorite_virtual_item(monkeypatch):
    from app.modules import douyin

    async def boom(*args, **kwargs):  # 收藏无需出网
        raise AssertionError("favorite 不应发起请求")

    monkeypatch.setattr(douyin, "following_list", boom)
    adapter = douyin.DouyinAdapter()
    out = await adapter.list_sources({"cookie": "c", "sec_user_id": "SEC123"}, "favorite")
    assert out == [{"remote_id": "SEC123", "title": "我的收藏"}]
    # 无 sec_user_id 时回退 "self"
    out2 = await adapter.list_sources({"cookie": "c"}, "favorite")
    assert out2 == [{"remote_id": "self", "title": "我的收藏"}]


@pytest.mark.asyncio
async def test_douyin_creator(monkeypatch):
    from app.modules import douyin

    calls: list[dict] = []

    async def fake_following_list(cookie, sec_user_id, offset="0", count="20"):
        calls.append({"cookie": cookie, "sec_user_id": sec_user_id, "offset": offset, "count": count})
        if offset == "0":
            # 抖小云按服务端返回的 offset 翻页，这里模拟平台给下一页游标
            return {
                "status_code": 0,
                "has_more": True,
                "offset": 2,
                "followings": [
                    {"sec_uid": "S1", "nickname": "博主1"},
                    {"sec_uid": "S2", "nickname": "博主2"},
                ],
            }
        return {"status_code": 0, "has_more": False, "offset": 3,
                "followings": [{"sec_uid": "S3", "nickname": "博主3"}]}

    monkeypatch.setattr(douyin, "following_list", fake_following_list)
    out = await douyin.DouyinAdapter().list_sources({"cookie": "ck", "sec_user_id": "SEC"}, "creator")
    assert out == [
        {"remote_id": "S1", "title": "博主1"},
        {"remote_id": "S2", "title": "博主2"},
        {"remote_id": "S3", "title": "博主3"},
    ]
    assert len(calls) == 2 and calls[0]["cookie"] == "ck" and calls[0]["sec_user_id"] == "SEC"


@pytest.mark.asyncio
async def test_douyin_creator_pagination_capped_at_50(monkeypatch):
    from app.modules import douyin

    async def always_more(cookie, sec_user_id, offset="0", count="20"):
        base = int(offset)
        return {
            "status_code": 0,
            "has_more": True,
            "followings": [
                {"sec_uid": f"S{base + i}", "nickname": f"n{base + i}"} for i in range(20)
            ],
        }

    monkeypatch.setattr(douyin, "following_list", always_more)
    out = await douyin.DouyinAdapter().list_sources({"cookie": "ck", "sec_user_id": "SEC"}, "creator")
    assert len(out) == 50, "关注列表最多取 50 条"
    assert out[0] == {"remote_id": "S0", "title": "n0"}


@pytest.mark.asyncio
async def test_douyin_creator_missing_sec_user_id_returns_empty(monkeypatch):
    from app.modules import douyin

    async def boom(*args, **kwargs):
        raise AssertionError("缺少 sec_user_id 时不应发起请求")

    monkeypatch.setattr(douyin, "following_list", boom)
    out = await douyin.DouyinAdapter().list_sources({"cookie": "ck"}, "creator")
    assert out == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["collection", "watch_later", ""])
async def test_douyin_unsupported_type_returns_empty(monkeypatch, source_type):
    from app.modules import douyin

    async def boom(*args, **kwargs):
        raise AssertionError("不支持的类型不应发起请求")

    monkeypatch.setattr(douyin, "following_list", boom)
    out = await douyin.DouyinAdapter().list_sources({"cookie": "ck", "sec_user_id": "SEC"}, source_type)
    assert out == []


@pytest.mark.asyncio
async def test_douyin_creator_network_error_does_not_raise(monkeypatch):
    from app.modules import douyin

    async def boom(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(douyin, "following_list", boom)
    out = await douyin.DouyinAdapter().list_sources({"cookie": "ck", "sec_user_id": "SEC"}, "creator")
    assert out == []


@pytest.mark.asyncio
async def test_douyin_creator_defensive_parsing(monkeypatch):
    """关注列表结构异常（缺 sec_uid / 非 0 状态码）时不崩、只跳过坏项。"""
    from app.modules import douyin

    async def weird(cookie, sec_user_id, offset="0", count="20"):
        return {
            "status_code": 0,
            "has_more": False,
            "followings": [{"nickname": "无ID"}, {"sec_uid": "OK"}],
        }

    monkeypatch.setattr(douyin, "following_list", weird)
    out = await douyin.DouyinAdapter().list_sources({"cookie": "ck", "sec_user_id": "SEC"}, "creator")
    assert out == [{"remote_id": "OK", "title": ""}]

    async def failed(cookie, sec_user_id, offset="1", count="10"):
        return {"status_code": 8, "followings": [{"sec_uid": "X"}]}

    monkeypatch.setattr(douyin, "following_list", failed)
    assert await douyin.DouyinAdapter().list_sources({"cookie": "ck", "sec_user_id": "SEC"}, "creator") == []


# ---------------------------------------------------------------- API 接线


def _make_bili_account(client) -> int:
    r = client.post(
        "/api/accounts",
        json={
            "platform": "bilibili",
            "display_name": "主号",
            "credential": {"sessdata": "s", "bili_jct": "j", "dedeuserid": "12345"},
            "skip_check": True,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_remote_sources_endpoint_forwards_source_type(client, monkeypatch):
    """端点把 source_type 透传给适配器，并解出明文凭证（适配器 mock，不出网）。"""
    aid = _make_bili_account(client)
    seen = {}

    async def fake_list_sources(self, credential, source_type):
        seen["source_type"] = source_type
        seen["mid"] = credential.get("dedeuserid")
        return [{"remote_id": "42", "title": "UP主A"}]

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.list_sources", fake_list_sources)
    r = client.get(f"/api/accounts/{aid}/remote-sources", params={"source_type": "creator"})
    assert r.status_code == 200, r.text
    assert r.json() == [{"remote_id": "42", "title": "UP主A"}]
    assert seen == {"source_type": "creator", "mid": "12345"}


def test_remote_sources_endpoint_defaults_to_favorite(client, monkeypatch):
    aid = _make_bili_account(client)
    seen = {}

    async def fake_list_sources(self, credential, source_type):
        seen["source_type"] = source_type
        return []

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.list_sources", fake_list_sources)
    r = client.get(f"/api/accounts/{aid}/remote-sources")
    assert r.status_code == 200, r.text
    assert r.json() == []
    assert seen["source_type"] == "favorite"


def test_published_at_helpers():
    """两个适配器的 Unix 秒 -> ISO 帮助函数（供 publish_after_days 过滤使用）。"""
    from app.modules.bilibili import _ts_to_iso as bts
    from app.modules.douyin import _ts_to_iso as dts

    assert bts(1700000000).startswith("2023-11-1")
    assert dts("1700000000").startswith("2023-11-1")
    assert bts(None) is None and bts(0) is None and dts("bad") is None
