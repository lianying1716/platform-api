"""知乎指标刷新：解析详情页内嵌的 js-initialData JSON（mock 页面，不联网）。"""

import json

import pytest


def _html(entities: dict) -> str:
    payload = {"initialState": {"entities": entities}}
    return (
        "<html><body><script id=\"js-initialData\" type=\"text/json\">"
        + json.dumps(payload, ensure_ascii=False)
        + "</script></body></html>"
    )


def test_extract_initial_data():
    from app.modules import zhihu_metrics

    data = zhihu_metrics.extract_initial_data(_html({"answers": {"1": {"voteup_count": 5}}}))
    assert data["initialState"]["entities"]["answers"]["1"]["voteup_count"] == 5
    assert zhihu_metrics.extract_initial_data("<html></html>") is None


def test_metrics_from_initial_data_answer():
    from app.modules import zhihu_metrics

    html = _html({"answers": {"1": {"voteup_count": 42, "comment_count": 7, "favlists_count": 3}}})
    m = zhihu_metrics.metrics_from_html(html, "answer", "1")
    assert m == {"play": 0, "like": 42, "comment": 7, "favorite": 3, "share": 0}


def test_metrics_from_initial_data_zvideo():
    from app.modules import zhihu_metrics

    html = _html({"zvideos": {"9": {"voteup_count": 5, "comment_count": 1, "play_count": 999}}})
    m = zhihu_metrics.metrics_from_html(html, "video", "9")
    assert m == {"play": 999, "like": 5, "comment": 1, "favorite": 0, "share": 0}


@pytest.mark.asyncio
async def test_fetch_zhihu_metrics_uses_browser(monkeypatch):
    from app.modules import zhihu_metrics, browser_fetch

    async def fake_html(**kwargs):
        assert kwargs["platform"] == "zhihu"
        assert kwargs["page_url"] == "https://zhuanlan.zhihu.com/p/3"
        return _html({"articles": {"3": {"voteup_count": 10, "comment_count": 2}}})

    monkeypatch.setattr(browser_fetch, "fetch_page_html", fake_html)
    m = await zhihu_metrics.fetch_metrics(
        "3", "d_c0=1", {"url": "https://zhuanlan.zhihu.com/p/3", "content_type": "article"}
    )
    assert m["like"] == 10 and m["comment"] == 2
