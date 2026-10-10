"""知乎评论抓取（mock 浏览器，不联网）：类型推断 + 评论归一化。"""

import pytest


def test_zhihu_comment_path_from_url():
    from app.modules import zhihu_comments

    assert zhihu_comments._comment_path("https://www.zhihu.com/question/1/answer/2", "2") == (
        "/api/v4/comment_v5/answers/2/root_comment"
    )
    assert zhihu_comments._comment_path("https://zhuanlan.zhihu.com/p/3", "3") == (
        "/api/v4/comment_v5/articles/3/root_comment"
    )
    assert zhihu_comments._comment_path("https://www.zhihu.com/zvideo/4", "4") == (
        "/api/v4/comment_v5/zvideos/4/root_comment"
    )


@pytest.mark.asyncio
async def test_zhihu_comments_normalize(monkeypatch):
    from app.modules import zhihu_comments, browser_fetch

    captured = {}

    async def fake_fetch_page(**kwargs):
        captured.update(kwargs)
        return {"data": [
            {"id": "c1", "content": "低赞", "like_count": 1, "created_time": 100,
             "author": {"id": "u1", "name": "甲", "avatar_url": "//a/x.jpg"}},
            {"id": "c2", "content": "高赞", "like_count": 99, "created_time": 200,
             "author": {"id": "u2", "name": "乙"}},
        ], "paging": {"is_end": True, "next": ""}}

    monkeypatch.setattr(browser_fetch, "fetch_until", fake_fetch_page)
    out = await zhihu_comments.fetch_comments(
        content_url="https://www.zhihu.com/question/1/answer/2",
        content_id="2", cookie="d_c0=1", pages=1, top_n=2,
    )
    assert [c["comment_id"] for c in out] == ["c2", "c1"]
    assert out[0]["nickname"] == "乙" and out[0]["content"] == "高赞"
    assert out[1]["avatar"] == "https://a/x.jpg"  # // 协议补全
    assert captured["api_token"] == "/api/v4/comment_v5/answers/2/root_comment"
