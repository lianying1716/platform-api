"""小红书评论抓取（mock httpx，不联网）：GET 签名 + 一级评论归一化。"""

import pytest


def _patch(monkeypatch, payloads):
    from app.core import rate_limit
    from app.modules.crawler import xhs_sign

    calls = []
    queue = list(payloads)
    monkeypatch.setattr(xhs_sign, "sign", lambda *a, **k: {"x-s": "S", "x-t": "T"})

    class Resp:
        def json(self):
            return queue.pop(0)

    class Client:
        async def get(self, url, params=None, headers=None, **kw):
            calls.append({"url": url, "params": params or {}, "headers": headers or {}})
            return Resp()

    class Ctx:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(rate_limit, "http_client", lambda *a, **k: Ctx())
    return calls


@pytest.mark.asyncio
async def test_xhs_comments_parse_and_sort(monkeypatch):
    from app.modules import xhs_comments

    payloads = [{
        "success": True,
        "data": {
            "has_more": False,
            "cursor": "",
            "comments": [
                {"id": "c1", "user_info": {"user_id": "u1", "nickname": "甲", "image": "//a/x.jpg"},
                 "content": "低赞", "like_count": "1", "create_time": 100},
                {"id": "c2", "user_info": {"user_id": "u2", "nickname": "乙"},
                 "content": "高赞", "like_count": "99", "create_time": 200},
            ],
        },
    }]
    calls = _patch(monkeypatch, payloads)
    out = await xhs_comments.fetch_comments(
        note_id="n1", xsec_token="TOK", cookie="a1=x", pages=1, top_n=2, interval=0
    )
    assert [c["comment_id"] for c in out] == ["c2", "c1"]
    assert out[0]["nickname"] == "乙" and out[0]["content"] == "高赞"
    assert calls[0]["url"].endswith("/api/sns/web/v2/comment/page")
    assert calls[0]["params"]["note_id"] == "n1"
    assert calls[0]["params"]["xsec_token"] == "TOK"


def test_xhs_sign_supports_get(monkeypatch):
    """xhshow 必须能以 GET 方式签名（评论接口是 GET）。"""
    import json
    from app.modules.crawler import xhs_sign

    captured = {}

    class FakeXhshow:
        def sign_xs(self, method, uri, a1, appid="xhs-pc-web", payload=None, timestamp=None, session=None):
            captured.update({"method": method, "uri": uri, "payload": payload})
            return "SIG"

        def sign_xsc(self, cookie_dict):
            return "COMMON"

        def get_x_t(self, timestamp=None):
            return 123

        def get_b3_trace_id(self):
            return "trace"

        @staticmethod
        def _parse_cookies(s):
            return {"a1": "abc"}

    monkeypatch.setattr("xhshow.Xhshow", lambda: FakeXhshow())
    headers = xhs_sign.sign("/api/sns/web/v2/comment/page", {"note_id": "n1"}, "a1=abc", "GET")
    assert captured["method"] == "GET"
    assert captured["payload"] == {"note_id": "n1"}
    assert headers["x-s"] == "SIG"
