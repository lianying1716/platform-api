"""快手评论抓取（mock httpx，不联网）：REST v2 契约 + 点赞降序。"""

import pytest


def _patch(monkeypatch, payloads):
    from app.core import rate_limit

    calls = []
    queue = list(payloads)

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
    return calls


@pytest.mark.asyncio
async def test_ks_comments_parse_and_sort(monkeypatch):
    from app.modules import ks_comments

    payloads = [{
        "result": 1,
        "pcursorV2": "no_more",
        "rootCommentsV2": [
            {"commentId": "c1", "authorId": "u1", "authorName": "甲", "content": "低赞",
             "likedCount": 1, "timestamp": 100},
            {"commentId": "c2", "authorId": "u2", "authorName": "乙", "content": "高赞",
             "likedCount": 99, "timestamp": 200, "headurl": "//a/x.jpg"},
        ],
    }]
    calls = _patch(monkeypatch, payloads)
    out = await ks_comments.fetch_comments(
        photo_id="v1", cookie="did=1", pages=1, per_page=20, top_n=2, interval=0
    )
    assert [c["comment_id"] for c in out] == ["c2", "c1"]
    assert out[0]["nickname"] == "乙" and out[0]["content"] == "高赞"
    assert out[0]["avatar"] == "https://a/x.jpg"
    assert calls[0]["url"] == "https://www.kuaishou.com/rest/v/photo/comment/list"
    assert calls[0]["json"] == {"photoId": "v1", "pcursor": ""}


@pytest.mark.asyncio
async def test_ks_comments_non_result_1_raises(monkeypatch):
    from app.modules import ks_comments

    _patch(monkeypatch, [{"result": 0, "message": "风控"}])
    with pytest.raises(RuntimeError, match="快手评论抓取失败"):
        await ks_comments.fetch_comments(photo_id="v1", cookie="c", pages=1, interval=0)
