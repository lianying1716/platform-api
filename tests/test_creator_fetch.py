"""创作者维度抓取：知乎/小红书按博主拉内容（浏览器监听，mock 不联网）。"""

import pytest


@pytest.mark.asyncio
async def test_zhihu_creator_answers(monkeypatch):
    from app.modules import creator_fetch, browser_fetch

    async def fake_until(**kw):
        assert kw["platform"] == "zhihu"
        assert "/members/token1/answers" in kw["api_token"]
        return {"data": [{
            "id": "a1", "question": {"id": "q1", "title": "露营装备"},
            "author": {"id": "u1", "name": "张三"}, "voteup_count": 42, "comment_count": 7,
            "created_time": 1700000000,
        }], "paging": {"is_end": True}}

    monkeypatch.setattr(browser_fetch, "fetch_until", fake_until)
    items = await creator_fetch.fetch_zhihu_creator(
        "token1", "d_c0=1", content_kind="answer", limit=10
    )
    assert items[0]["external_id"] == "a1"
    assert items[0]["title"] == "露营装备"
    assert items[0]["author_name"] == "张三"
    assert items[0]["metrics"]["like"] == 42
    assert items[0]["url"] == "https://www.zhihu.com/question/q1/answer/a1"


@pytest.mark.asyncio
async def test_zhihu_adapter_creator_branch(monkeypatch):
    from app.modules import crawl_platforms, creator_fetch

    async def fake(**kw):
        return [{"external_id": "a9", "content_type": "answer", "title": "t",
                 "author_name": "n", "author_id": "u", "url": "u", "cover_url": "",
                 "metrics": {}}]

    monkeypatch.setattr(creator_fetch, "fetch_zhihu_creator", fake)
    items = await crawl_platforms.ZhihuAdapter().fetch_source(
        {"cookie": "z_c0=1"}, "creator", "token1", 10
    )
    assert items[0]["external_id"] == "a9"


@pytest.mark.asyncio
async def test_xhs_creator_notes(monkeypatch):
    from app.modules import creator_fetch, browser_fetch

    async def fake_until(**kw):
        assert kw["platform"] == "xhs"
        assert "/user_posted" in kw["api_token"]
        return {"notes": [{
            "note_id": "n1", "display_title": "露营日记", "user": {"nickname": "李四", "user_id": "u2"},
            "cover": {"url_default": "https://c/x.jpg"}, "interact_info": {"liked_count": "9"},
        }], "has_more": False, "cursor": ""}

    monkeypatch.setattr(browser_fetch, "fetch_until", fake_until)
    items = await creator_fetch.fetch_xhs_creator("u2", "web_session=x", limit=10)
    assert items[0]["external_id"] == "n1"
    assert items[0]["title"] == "露营日记"
    assert items[0]["metrics"]["like"] == 9
    assert items[0]["url"] == "https://www.xiaohongshu.com/explore/n1"


@pytest.mark.asyncio
async def test_xhs_creator_accepts_nested_data_response(monkeypatch):
    """线上 user_posted 响应是 {success, data:{notes}}，不能只认顶层 notes。"""
    from app.modules import creator_fetch, browser_fetch

    async def fake_until(**kw):
        return {"success": True, "data": {"notes": [{
            "note_id": "n2", "display_title": "嵌套响应", "user": {"nickname": "王五", "user_id": "u3"},
            "interact_info": {"liked_count": 3},
        }]}}

    monkeypatch.setattr(browser_fetch, "fetch_until", fake_until)
    items = await creator_fetch.fetch_xhs_creator("u3", "web_session=x", limit=10)
    assert [i["external_id"] for i in items] == ["n2"]
