"""指标刷新：按平台拉最新互动数并写入 content_metric_snapshot（mock，不联网）。"""

import pytest


def test_comment_platforms_still_whitelisted():
    from app.services.comment_gate import COMMENT_PLATFORMS

    assert COMMENT_PLATFORMS == frozenset({"douyin", "bilibili", "kuaishou", "xhs", "zhihu"})


@pytest.mark.asyncio
async def test_refresh_bilibili_metrics(monkeypatch):
    from app.modules import content_metrics

    calls = []

    class Resp:
        def json(self):
            return {"code": 0, "data": {"stat": {
                "view": 1234, "like": 56, "reply": 7, "favorite": 89, "share": 1, "coin": 2,
            }}}

    class Client:
        async def get(self, url, params=None, cookies=None):
            calls.append({"url": url, "params": params or {}})
            return Resp()

    class Ctx:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(content_metrics, "_bili_http", lambda: Ctx())
    m = await content_metrics.fetch_bilibili_metrics("BV1xx", {"sessdata": "s"})
    assert m == {"play": 1234, "like": 56, "comment": 7, "favorite": 89, "share": 1}
    assert calls[0]["params"] == {"bvid": "BV1xx"}


@pytest.mark.asyncio
async def test_refresh_douyin_metrics_uses_browser_fetch(monkeypatch):
    from app.modules import content_metrics, douyin_browser_fetch

    async def fake_fetch_json(path, params, cookie, *, method="GET", referer="https://www.douyin.com/"):
        assert path == "/aweme/v1/web/aweme/detail/"
        assert params["aweme_id"] == "7001"
        return {"aweme_detail": {"statistics": {
            "play_count": 10, "digg_count": 20, "comment_count": 3,
            "collect_count": 4, "share_count": 5,
        }}}

    monkeypatch.setattr(douyin_browser_fetch, "fetch_json", fake_fetch_json)
    m = await content_metrics.fetch_douyin_metrics("7001", "c=1")
    assert m == {"play": 10, "like": 20, "comment": 3, "favorite": 4, "share": 5}


@pytest.mark.asyncio
async def test_unsupported_platform_returns_none():
    from app.modules import content_metrics

    assert await content_metrics.fetch_metrics("zhihu", "x", "c") is None


@pytest.mark.asyncio
async def test_refresh_kuaishou_metrics(monkeypatch):
    from app.modules import content_metrics
    from app.core import rate_limit

    calls = []

    class Resp:
        def json(self):
            return {"data": {"visionVideoDetail": {"status": 1, "photo": {
                "id": "v1", "viewCount": 100, "likeCount": 9, "commentCount": 2,
            }}}}

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
    m = await content_metrics.fetch_kuaishou_metrics("v1", "did=1")
    assert m == {"play": 100, "like": 9, "comment": 2, "favorite": 0, "share": 0}
    assert calls[0]["json"]["operationName"] == "visionVideoDetail"


def test_refresh_metrics_endpoint_enqueues_job(client, session):
    """API 端点应入队 refresh_metrics 任务（形成完整闭环，而非空模块）。"""
    from app.models import ContentItem, JobQueue, MediaAsset

    c = ContentItem(account_id=1, platform="bilibili", external_id="BV1", content_type="video")
    session.add(c)
    session.flush()
    a = MediaAsset(content_id=c.id, download_status="done")
    session.add(a)
    session.commit()

    resp = client.post(f"/api/assets/{a.id}/refresh-metrics")
    assert resp.status_code == 200, resp.text
    assert resp.json()["job_id"]
    job = session.query(JobQueue).filter_by(job_type="refresh_metrics").one()
    assert job.payload["content_id"] == c.id
