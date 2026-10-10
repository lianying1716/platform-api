"""B站「稍后再看」订阅源（mock httpx，不联网）。对齐 bili-sync watch_later。"""

import pytest


def _item(bvid="BV1", title="露营", author="UP", mid=1, duration=100):
    return {
        "bvid": bvid,
        "title": title,
        "owner": {"name": author, "mid": mid},
        "pic": "//i.hdslb.com/x.jpg",
        "duration": duration,
        "pubdate": 1700000000,
        "stat": {"view": 10, "favorite": 2, "danmaku": 1},
    }


def _patch(monkeypatch, payload):
    from app.modules import bilibili

    calls = []

    class Resp:
        status_code = 200

        def json(self):
            return payload

    class Client:
        async def get(self, url, params=None, cookies=None):
            calls.append({"url": url, "params": params or {}})
            return Resp()

    class Ctx:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(bilibili, "_http", lambda: Ctx())
    return calls


@pytest.mark.asyncio
async def test_list_sources_includes_watch_later(monkeypatch):
    from app.modules import bilibili

    calls = _patch(monkeypatch, {"code": 0, "data": {"list": [_item()]}})
    out = await bilibili.BilibiliAdapter().list_sources(
        {"sessdata": "s", "bili_jct": "j", "dedeuserid": "9"}, "watch_later"
    )
    assert out == [{"remote_id": "watch_later", "title": "稍后再看"}]
    # 单例源不需要列举接口：列表拉取发生在 fetch_source 阶段。
    assert calls == []


@pytest.mark.asyncio
async def test_fetch_watch_later_videos(monkeypatch):
    from app.modules import bilibili

    _patch(monkeypatch, {"code": 0, "data": {"list": [_item("BV9", "露营录像")]}})
    items = await bilibili.BilibiliAdapter().fetch_source(
        {"sessdata": "s", "bili_jct": "j"}, "watch_later", "watch_later", 20
    )
    assert [i["external_id"] for i in items] == ["BV9"]
    assert items[0]["title"] == "露营录像"
    assert items[0]["author_name"] == "UP"
    assert items[0]["url"] == "https://www.bilibili.com/video/BV9"
    assert items[0]["metrics"]["play"] == 10


@pytest.mark.asyncio
async def test_watch_later_empty_is_safe(monkeypatch):
    from app.modules import bilibili

    _patch(monkeypatch, {"code": 0, "data": {"list": []}})
    items = await bilibili.BilibiliAdapter().fetch_source(
        {"sessdata": "s", "bili_jct": "j"}, "watch_later", "watch_later", 20
    )
    assert items == []
