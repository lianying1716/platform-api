"""抖音评论抓取解析测试（不联网：浏览器同源请求用桩替代）。"""
import asyncio


def test_fetch_comments_parses_and_sorts(monkeypatch):
    from app.modules.crawler import client
    from app.modules import douyin_browser_fetch

    pages_payload = [
        {"comments": [
            {"cid": "c1", "text": "低赞", "digg_count": 1, "reply_id": "0",
             "user": {"uid": "u1", "nickname": "甲"}, "ip_label": "北京", "create_time": 100},
            {"cid": "c2", "text": "高赞", "digg_count": 99, "reply_id": "0",
             "user": {"uid": "u2", "nickname": "乙"}, "ip_label": "上海", "create_time": 200},
        ], "has_more": 0},
    ]

    async def fake_fetch_json(path, params, cookie, *, method="GET", referer="https://www.douyin.com/"):
        assert path == "/aweme/v1/web/comment/list/"
        assert params["aweme_id"] == "a1"
        return pages_payload.pop(0)

    monkeypatch.setattr(douyin_browser_fetch, "fetch_json", fake_fetch_json)
    out = asyncio.run(client.fetch_comments(aweme_id="a1", cookie="c", pages=1, per_page=20, top_n=2, interval=0))
    assert [c["comment_id"] for c in out] == ["c2", "c1"]  # 按点赞降序
    assert out[0]["nickname"] == "乙" and out[0]["content"] == "高赞"
