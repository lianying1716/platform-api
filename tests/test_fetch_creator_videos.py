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
    # 关键回归：路径**不能带尾斜杠**。带尾斜杠会被 Argus 拦成 403
    # （实测 Blocked by ArgusSecurityPlugin Uifid Not Found）。
    assert "/aweme/v1/web/aweme/post?" in calls[0]["url"]
    assert "/aweme/v1/web/aweme/post/?" not in calls[0]["url"]
    # 不真算签名：抖小云同样不签名。
    # 注意它的参数模板里**保留 a_bogus 空键**（"带空键"与"完全不带"服务端处理不同），
    # 所以这里断言的是"没有真的签名值"，而不是"没有这个键"。
    assert "a_bogus=&" in calls[0]["url"] or calls[0]["url"].endswith("a_bogus=")
    assert "ABOGUS" not in calls[0]["url"]


@pytest.mark.asyncio
async def test_douyin_creator_argus_block_retries_with_signature(monkeypatch):
    """万一又被 Argus 拦（平台改版），应带上 a_bogus 重试一次，而不是直接失败。"""
    from app.modules import douyin

    calls = []

    class _Resp:
        def __init__(self, status, text, payload=None):
            self.status_code = status
            self.text = text
            self._payload = payload

        def json(self):
            return self._payload

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            calls.append(url)
            # 模板里本来就有空的 a_bogus= 键，所以要看"有没有真签名值"
            if "a_bogus=FAKEBOGUS" not in url:
                return _Resp(403, "Blocked by ArgusSecurityPlugin Uifid Not Found")
            return _Resp(200, "", {"status_code": 0, "has_more": False,
                                   "aweme_list": [{"aweme_id": "9001", "desc": "重试拿到",
                                                   "statistics": {}, "author": {}}]})

    monkeypatch.setattr(douyin, "_client", lambda *a, **k: _C())
    monkeypatch.setattr(douyin.douyin_sign, "get_a_bogus", lambda *a, **k: "FAKEBOGUS")

    items = await douyin.DouyinAdapter().fetch_source({"cookie": "c=1"}, "creator", "SEC1", 10)
    assert len(calls) == 2, "被拦后应重试一次"
    assert "a_bogus=FAKEBOGUS" in calls[1]
    assert items[0]["external_id"] == "9001"
