"""关注博主富列表（list_followings）专项测试：真实头像/昵称/主页 + 粉丝数（mock httpx）。"""

import pytest


class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
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
                # 粉丝接口按 vmid 区分
                if callable(payload):
                    return _FakeResp(payload((params or {}).get("vmid") or (params or {}).get("sec_user_id")))
                return _FakeResp(payload)
        raise AssertionError(f"未预置的 URL: {url}")


def _patch_client(monkeypatch, module, routes):
    calls: list[dict] = []
    monkeypatch.setattr(module, "_client", lambda *a, **k: _FakeClient(routes, calls))
    return calls


# ---------------------------------------------------------------- B站


@pytest.mark.asyncio
async def test_bilibili_list_followings_rich(monkeypatch):
    from app.modules import bilibili

    followings = {
        "code": 0,
        "data": {
            "total": 2,
            "list": [
                {"mid": 1001, "uname": "老饭骨", "face": "http://i0.hdslb.com/av1.jpg",
                 "sign": "好好吃饭", "attribute": 0},
                {"mid": 1002, "uname": "影视飓风", "face": "http://i0.hdslb.com/av2.jpg",
                 "sign": "影像科技", "attribute": 0},
            ],
        },
    }

    def stat(vmid):
        return {"code": 0, "data": {"mid": int(vmid), "follower": 123456}}

    calls = _patch_client(
        monkeypatch, bilibili, {"relation/followings": followings, "relation/stat": stat}
    )
    adapter = bilibili.BilibiliAdapter()
    out = await adapter.list_followings({"dedeuserid": "999", "sessdata": "s", "bili_jct": "j"})

    assert [c["author_id"] for c in out] == ["1001", "1002"]
    c0 = out[0]
    assert c0["author_name"] == "老饭骨"
    assert c0["avatar"] == "http://i0.hdslb.com/av1.jpg"
    assert c0["signature"] == "好好吃饭"
    assert c0["home_url"] == "https://space.bilibili.com/1001"
    # 粉丝数由 API 层带 24h 缓存补全（适配器只负责列表），此处不再内联请求
    assert c0["follower_count"] == 0
    # 分页参数
    fcall = next(c for c in calls if "relation/followings" in c["url"])
    assert str(fcall["params"]["pn"]) == "1" and int(fcall["params"]["ps"]) >= 20 and fcall["params"]["mid"] == "999"


@pytest.mark.asyncio
async def test_bilibili_list_followings_pagination(monkeypatch):
    from app.modules import bilibili

    pages = {
        1: {"code": 0, "data": {"total": 3, "list": [
            {"mid": 1, "uname": "a", "face": "f1"},
            {"mid": 2, "uname": "b", "face": "f2"},
        ]}},
        2: {"code": 0, "data": {"total": 3, "list": [{"mid": 3, "uname": "c", "face": "f3"}]}},
    }

    class _Pag:
        def __init__(self, calls):
            self.calls = calls

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, cookies=None):
            self.calls.append({"url": url, "params": params or {}})
            if "relation/followings" in url:
                return _FakeResp(pages[int((params or {}).get("pn", 1))])
            return _FakeResp({"code": 0, "data": {"follower": 10}})

    calls: list[dict] = []
    monkeypatch.setattr(bilibili, "_client", lambda *a, **k: _Pag(calls))
    adapter = bilibili.BilibiliAdapter()
    out = await adapter.list_followings({"dedeuserid": "999"}, limit=50)
    assert [c["author_id"] for c in out] == ["1", "2", "3"]
    got_pages = sorted(int(c["params"]["pn"]) for c in calls if "relation/followings" in c["url"])
    assert got_pages == [1, 2]


@pytest.mark.asyncio
async def test_bilibili_list_followings_no_mid_returns_empty():
    from app.modules import bilibili

    adapter = bilibili.BilibiliAdapter()
    assert await adapter.list_followings({"sessdata": "s"}) == []


# ---------------------------------------------------------------- 抖音


@pytest.mark.asyncio
async def test_douyin_list_followings_rich(monkeypatch):
    from app.modules import douyin

    payload = {
        "status_code": 0,
        "has_more": False,
        "followings": [
            {
                "sec_uid": "SEC1", "nickname": "老饭骨",
                "signature": "国宴大师",
                "enterprise_verify_reason": "官方认证",
                "avatar_larger": {"url_list": ["https://p3.douyinpic.com/av1.jpeg"]},
            },
            {
                "sec_uid": "SEC2", "nickname": "帕梅拉",
                "avatar_larger": {"url_list": ["https://p3.douyinpic.com/av2.jpeg"]},
            },
        ],
    }
    calls = _patch_client(monkeypatch, douyin, {"following/list": payload})
    adapter = douyin.DouyinAdapter()
    out = await adapter.list_followings({"cookie": "c=1", "sec_user_id": "SELF"})

    assert [c["author_id"] for c in out] == ["SEC1", "SEC2"]
    c0 = out[0]
    assert c0["author_name"] == "老饭骨"
    assert c0["avatar"] == "https://p3.douyinpic.com/av1.jpeg"
    assert c0["signature"] == "国宴大师"
    assert c0["verified"] is True
    assert c0["home_url"] == "https://www.douyin.com/user/SEC1"
    # 无签名时粉丝/获赞留 0（best-effort，不阻塞）
    assert c0["follower_count"] == 0
    assert c0["like_count"] == 0


@pytest.mark.asyncio
async def test_douyin_list_followings_no_secuid_returns_empty():
    from app.modules import douyin

    adapter = douyin.DouyinAdapter()
    assert await adapter.list_followings({"cookie": "c=1"}) == []


@pytest.mark.asyncio
async def test_douyin_list_followings_tolerates_errors(monkeypatch):
    import httpx

    from app.modules import douyin

    _patch_client(monkeypatch, douyin, {"following/list": httpx.ConnectError("boom")})
    adapter = douyin.DouyinAdapter()
    # 网络异常降级为空列表（不抛）
    assert await adapter.list_followings({"cookie": "c=1", "sec_user_id": "SELF"}) == []
