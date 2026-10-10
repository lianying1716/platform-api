"""B站评论抓取（mock httpx，不联网）：bvid→aid 解析 + 一级评论归一化。"""

import asyncio

import pytest


def _patch_http(monkeypatch, routes):
    from app.modules import bilibili

    calls = []

    class Resp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class Client:
        async def get(self, url, params=None, cookies=None, **kw):
            calls.append({"url": url, "params": params or {}})
            for key, payload in routes.items():
                if key in url:
                    return Resp(payload)
            raise AssertionError(f"未预置 URL: {url}")

    class Ctx:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(bilibili, "_http", lambda: Ctx())
    return calls


@pytest.mark.asyncio
async def test_bili_fetch_comments_resolves_aid_and_normalizes(monkeypatch):
    from app.modules import bili_comments

    routes = {
        "/x/web-interface/view": {"code": 0, "data": {"aid": 9527}},
        "/x/v2/reply/wbi/main": {
            "code": 0,
            "data": {
                "cursor": {"is_end": True, "next": 0},
                "replies": [
                    {"rpid": 11, "member": {"uname": "甲", "mid": "1", "avatar": "//a/x.jpg"},
                     "content": {"message": "低赞"}, "like": 1, "ctime": 100, "rcount": 0},
                    {"rpid": 22, "member": {"uname": "乙", "mid": "2", "avatar": "//a/y.jpg"},
                     "content": {"message": "高赞"}, "like": 99, "ctime": 200, "rcount": 0},
                ],
            },
        },
    }
    calls = _patch_http(monkeypatch, routes)
    out = await bili_comments.fetch_comments(bvid="BV1xx", cookie="SESSDATA=s", pages=1, per_page=20, top_n=2)

    assert [c["comment_id"] for c in out] == ["22", "11"]
    assert out[0]["nickname"] == "乙" and out[0]["content"] == "高赞"
    assert out[0]["user_id"] == "2"
    # oid 必须是 aid(9527)，不是 bvid
    body = calls[1]["params"]
    assert body["oid"] == 9527


def test_comment_platforms_includes_bilibili():
    from app.services.comment_gate import COMMENT_PLATFORMS

    assert "bilibili" in COMMENT_PLATFORMS
    assert "douyin" in COMMENT_PLATFORMS
