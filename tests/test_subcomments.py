"""二级评论（子评论）：B站 /reply/reply、快手 /rest/v/photo/comment/sublist。"""

import pytest


@pytest.mark.asyncio
async def test_bili_sub_comments_parsed(monkeypatch):
    from app.modules import bili_comments

    calls = []

    class Resp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class Client:
        async def get(self, url, params=None, cookies=None, **kw):
            calls.append({"url": url, "params": params or {}})
            if "web-interface/view" in url:
                return Resp({"code": 0, "data": {"aid": 9527}})
            if "reply/wbi/main" in url:
                return Resp({"code": 0, "data": {
                    "cursor": {"is_end": True, "next": 0},
                    "replies": [{"rpid": 11, "member": {"uname": "甲", "mid": "1"},
                                 "content": {"message": "主评论"}, "like": 5,
                                 "ctime": 100, "rcount": 2}],
                }})
            return Resp({"code": 0, "data": {"replies": [
                {"rpid": 21, "member": {"uname": "乙", "mid": "2"},
                 "content": {"message": "回复一"}, "like": 1, "ctime": 101,
                 "parent": 11, "root": 11},
            ], "page": {"count": 1}}})

    class Ctx:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    from app.modules import bilibili
    monkeypatch.setattr(bilibili, "_http", lambda: Ctx())
    out = await bili_comments.fetch_comments(
        bvid="BV1", cookie="SESSDATA=s", pages=1, top_n=10, interval=0,
        with_sub_comments=True,
    )
    ids = [c["comment_id"] for c in out]
    assert "11" in ids and "21" in ids
    sub = next(c for c in out if c["comment_id"] == "21")
    assert sub["parent_comment_id"] == "11"
    assert any(c["url"].endswith("/x/v2/reply/reply") for c in calls)


@pytest.mark.asyncio
async def test_ks_sub_comments_parsed(monkeypatch):
    from app.modules import ks_comments
    from app.core import rate_limit

    calls = []
    queue = [
        {"result": 1, "pcursorV2": "no_more", "rootCommentsV2": [
            {"commentId": "11", "authorId": "1", "authorName": "甲", "content": "主评论",
             "likedCount": 5, "timestamp": 100, "subCommentCount": 1},
        ]},
        {"result": 1, "pcursorV2": "no_more", "subCommentsV2": [
            {"commentId": "21", "authorId": "2", "authorName": "乙", "content": "回复一",
             "likedCount": 1, "timestamp": 101},
        ]},
    ]

    class Resp:
        def json(self):
            return queue.pop(0)

    class Client:
        async def post(self, url, json=None, **kw):
            calls.append({"url": url, "json": json})
            return Resp()

    class Ctx:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(rate_limit, "http_client", lambda *a, **k: Ctx())
    out = await ks_comments.fetch_comments(
        photo_id="v1", cookie="did=1", pages=1, top_n=10, interval=0,
        with_sub_comments=True,
    )
    ids = [c["comment_id"] for c in out]
    assert "11" in ids and "21" in ids
    sub = next(c for c in out if c["comment_id"] == "21")
    assert sub["parent_comment_id"] == "11"
    assert any(c["url"].endswith("/rest/v/photo/comment/sublist") for c in calls)


@pytest.mark.asyncio
async def test_sub_comments_off_by_default(monkeypatch):
    """默认不抓子评论，保持既有行为与请求量。"""
    from app.modules import bili_comments

    seen = []

    class Resp:
        def __init__(self, p):
            self._p = p

        def json(self):
            return self._p

    class Client:
        async def get(self, url, params=None, cookies=None, **kw):
            seen.append(url)
            if "web-interface/view" in url:
                return Resp({"code": 0, "data": {"aid": 1}})
            if "reply/wbi/main" in url:
                return Resp({"code": 0, "data": {"cursor": {"is_end": True},
                            "replies": [{"rpid": 11, "member": {}, "content": {}, "like": 0,
                                         "rcount": 5}]}})
            return Resp({"code": 0, "data": {"replies": [], "page": {"count": 0}}})

    class Ctx:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    from app.modules import bilibili
    monkeypatch.setattr(bilibili, "_http", lambda: Ctx())
    await bili_comments.fetch_comments(bvid="BV1", cookie="c", pages=1, top_n=10, interval=0)
    assert not any(u.endswith("/x/v2/reply/reply") for u in seen)
