"""creator 订阅拉取（B站 UP 投稿 / 抖音博主视频）专项测试（mock httpx，不联网）。"""

import httpx
import pytest


class _FakeResp:
    status_code = 200

    def __init__(self, payload, text=None):
        self._payload = payload
        self.text = text if text is not None else ""

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _patch_bili(monkeypatch, routes):
    from app.modules import bilibili

    calls: list[dict] = []

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, cookies=None):
            calls.append({"url": url, "params": params or {}})
            for key, payload in routes.items():
                if key in url:
                    return _FakeResp(payload)
            raise AssertionError(f"未预置 URL: {url}")

    monkeypatch.setattr(bilibili, "_client", lambda *a, **k: _C())
    # WBI 签名桩：直接回传参数 + 假 w_rid
    monkeypatch.setattr(bilibili.bili_sign, "fetch_wbi_keys", lambda *a, **k: _async((("img", "sub"))))
    monkeypatch.setattr(bilibili.bili_sign, "sign", lambda params, i, s: {**params, "w_rid": "FAKE"})
    return calls


def _async(value):
    async def _coro():
        return value

    return _coro()


@pytest.mark.asyncio
async def test_bilibili_fetch_creator_videos(monkeypatch):
    from app.modules import bilibili

    payload = {
        "code": 0,
        "data": {
            "list": {
                "vlist": [
                    {"bvid": "BV1aa", "title": "第1期", "author": "影视飓风", "mid": 946974,
                     "pic": "//i0.hdslb.com/c1.jpg", "created": 1703317974,
                     "play": 1000, "comment": 12, "video_review": 3},
                ]
            },
            "page": {"count": 1},
        },
    }
    calls = _patch_bili(monkeypatch, {"space/wbi/arc/search": payload})
    adapter = bilibili.BilibiliAdapter()
    items = await adapter.fetch_source({"dedeuserid": "1", "sessdata": "s", "bili_jct": "j"}, "creator", "946974", 10)

    assert len(items) == 1
    it = items[0]
    assert it["external_id"] == "BV1aa"
    assert it["content_type"] == "video"
    assert it["title"] == "第1期"
    assert it["author_id"] == "946974"
    assert it["url"] == "https://www.bilibili.com/video/BV1aa"
    assert it["cover_url"].startswith("https://")
    assert it["published_at"].startswith("2023-12-23")
    assert it["metrics"]["play"] == 1000
    # 请求形状：wbi 签名参数 + mid
    call = calls[0]
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(call["url"]).query)
    assert "space/wbi/arc/search" in call["url"]
    assert qs["mid"] == ["946974"] and qs["w_rid"] == ["FAKE"]


@pytest.mark.asyncio
async def test_bilibili_fetch_creator_risk_raises(monkeypatch):
    from app.modules import bilibili

    _patch_bili(monkeypatch, {"space/wbi/arc/search": {"code": -352, "message": "风控校验失败"}})
    adapter = bilibili.BilibiliAdapter()
    with pytest.raises(RuntimeError, match="风控"):
        await adapter.fetch_source({"dedeuserid": "1"}, "creator", "946974", 10)


@pytest.mark.asyncio
async def test_bilibili_still_supports_favorite(monkeypatch):
    """回归：favorite 路径不受影响。"""
    from app.modules import bilibili

    _patch_bili(
        monkeypatch,
        {
            "fav/resource/list": {
                "code": 0,
                "data": {"medias": [{"bvid": "BVfav", "title": "收藏视频",
                                     "upper": {"mid": 2, "name": "x"}, "pubtime": 1703317974}],
                         "has_more": False},
            }
        },
    )
    adapter = bilibili.BilibiliAdapter()
    items = await adapter.fetch_source({"dedeuserid": "1"}, "favorite", "123", 10)
    assert [i["external_id"] for i in items] == ["BVfav"]


def _patch_douyin(monkeypatch, routes, sign_ok=True):
    from app.modules import douyin

    calls: list[dict] = []

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, cookies=None):
            calls.append({"url": url, "params": params or {}})
            for key, payload in routes.items():
                if key in url:
                    if isinstance(payload, Exception):
                        raise payload
                    return _FakeResp(payload)
            raise AssertionError(f"未预置 URL: {url}")

    monkeypatch.setattr(douyin, "_client", lambda *a, **k: _C())

    def _sign(url, params, ua):
        if not sign_ok:
            raise RuntimeError("签名失败")
        return "ABOGUS"

    monkeypatch.setattr(douyin.douyin_sign, "get_a_bogus", _sign)
    return calls


@pytest.mark.asyncio
async def test_douyin_fetch_creator_videos(monkeypatch):
    from app.modules import douyin

    payload = {
        "status_code": 0,
        "has_more": False,
        "aweme_list": [
            {"aweme_id": "7001", "desc": "美食教程",
             "author": {"sec_uid": "SEC1", "nickname": "老饭骨"},
             "video": {"cover": {"url_list": ["https://p3.douyinpic.com/c.jpg"]}},
             "create_time": 1703317974,
             "statistics": {"play_count": 5, "digg_count": 3, "comment_count": 1,
                            "collect_count": 2, "share_count": 1}},
        ],
    }
    calls = _patch_douyin(monkeypatch, {"aweme/post": payload})
    adapter = douyin.DouyinAdapter()
    items = await adapter.fetch_source({"cookie": "c=1"}, "creator", "SEC1", 10)

    assert len(items) == 1
    it = items[0]
    assert it["external_id"] == "7001"
    assert it["author_id"] == "SEC1"
    assert it["cover_url"] == "https://p3.douyinpic.com/c.jpg"
    assert it["metrics"]["play"] == 5
    assert it["published_at"].startswith("2023-12-23")
    assert "a_bogus" in calls[0]["url"]


@pytest.mark.asyncio
async def test_douyin_fetch_creator_sign_failure_is_readable(monkeypatch):
    from app.modules import douyin

    _patch_douyin(monkeypatch, {"aweme/post": {"status_code": 0, "aweme_list": []}}, sign_ok=False)
    adapter = douyin.DouyinAdapter()
    with pytest.raises(RuntimeError, match="签名"):
        await adapter.fetch_source({"cookie": "c=1"}, "creator", "SEC1", 10)

# ---------------------------------------------------------------- Argus 兜底

@pytest.mark.asyncio
async def test_douyin_argus_block_falls_back_to_browser(monkeypatch):
    """纯 HTTP 被 Argus 拦（403 Uifid Not Found）时，应回退浏览器同源 fetch。

    实测：/aweme/v1/web/aweme/post/ 带 a_bogus 仍返回
    403 Blocked by ArgusSecurityPlugin Uifid Not Found；
    页面内同源 fetch 返回 200。所以被拦时必须走浏览器兜底，而不是直接报错。
    """
    from app.modules import douyin

    class _Blocked:
        status_code = 403
        text = "Blocked by ArgusSecurityPlugin Uifid Not Found"

        def json(self):
            raise AssertionError("被拦时不应解析 JSON")

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _Blocked()

    monkeypatch.setattr(douyin, "_client", lambda *a, **k: _C())
    monkeypatch.setattr(douyin.douyin_sign, "get_a_bogus", lambda *a, **k: "fake")

    called = {}

    async def fake_fetch_json(path, params, cookie, referer=""):
        called["path"] = path
        called["sec"] = params.get("sec_user_id")
        return {
            "status_code": 0,
            "has_more": False,
            "aweme_list": [{
                "aweme_id": "7300000000000000001",
                "desc": "兜底拿到的作品",
                "create_time": 1700000000,
                "statistics": {"digg_count": 12, "comment_count": 3},
                "author": {"nickname": "某博主", "sec_uid": "SECU"},
                "video": {"cover": {"url_list": ["https://c/1.jpg"]}},
            }],
        }

    monkeypatch.setattr("app.modules.douyin_browser_fetch.fetch_json", fake_fetch_json)

    items = await douyin.DouyinAdapter()._fetch_creator_videos("sessionid=x", "SECU", 10)
    assert called.get("path") == "/aweme/v1/web/aweme/post/"
    assert called.get("sec") == "SECU"
    assert len(items) == 1
    assert items[0]["external_id"] == "7300000000000000001"
    assert items[0]["title"] == "兜底拿到的作品"
    assert items[0]["metrics"]["like"] == 12
