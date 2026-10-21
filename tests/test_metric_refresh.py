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
async def test_refresh_douyin_metrics_keeps_missing_fields_unknown(monkeypatch):
    from app.modules import content_metrics, douyin_browser_fetch

    async def fake_fetch_json(*args, **kwargs):
        return {"aweme_detail": {"statistics": {"digg_count": 20, "play_count": 0}}}

    monkeypatch.setattr(douyin_browser_fetch, "fetch_json", fake_fetch_json)
    m = await content_metrics.fetch_douyin_metrics("7001", "c=1")
    assert m == {"play": 0, "like": 20, "comment": None, "favorite": None, "share": None}


@pytest.mark.asyncio
async def test_unsupported_platform_returns_none():
    from app.modules import content_metrics

    assert await content_metrics.fetch_metrics("wechat_mp", "x", "c") is None


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
    assert m == {"play": 100, "like": 9, "comment": 2, "favorite": None, "share": None}
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


def test_bulk_refresh_targets_unknown_and_missing_latest_play(client, session):
    from datetime import datetime

    from app.models import ContentItem, ContentMetricSnapshot, JobQueue

    known = ContentItem(account_id=1, platform="douyin", external_id="known", content_type="video")
    unknown = ContentItem(account_id=1, platform="douyin", external_id="unknown", content_type="video")
    no_snapshot = ContentItem(account_id=1, platform="douyin", external_id="nosnap", content_type="video")
    session.add_all([known, unknown, no_snapshot])
    session.flush()
    old = datetime(2026, 10, 20, 12, 0, 0)
    new = datetime(2026, 10, 21, 12, 0, 0)
    session.add_all(
        [
            ContentMetricSnapshot(content_id=known.id, play=0, captured_at=old),
            ContentMetricSnapshot(content_id=unknown.id, play=None, captured_at=new),
        ]
    )
    session.commit()

    resp = client.post("/api/assets/refresh-metrics/bulk", json={"platform": "douyin"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["matched"] == 2
    jobs = session.query(JobQueue).filter_by(job_type="refresh_metrics").all()
    assert [j.payload["content_id"] for j in jobs] == [unknown.id, no_snapshot.id]

    again = client.post("/api/assets/refresh-metrics/bulk", json={"platform": "douyin"})
    assert again.status_code == 200, again.text
    assert session.query(JobQueue).filter_by(job_type="refresh_metrics").count() == 2


def test_bulk_refresh_excludes_content_without_a_play_metric(client, session):
    """Articles and platforms without metric refresh must not create permanent failures."""
    from app.models import ContentItem, JobQueue

    video = ContentItem(
        account_id=1, platform="douyin", external_id="video", content_type="video"
    )
    article = ContentItem(
        account_id=1, platform="zhihu", external_id="answer", content_type="answer"
    )
    wechat = ContentItem(
        account_id=1, platform="wechat_mp", external_id="article", content_type="article"
    )
    session.add_all([video, article, wechat])
    session.commit()

    resp = client.post("/api/assets/refresh-metrics/bulk", json={"limit": 500})
    assert resp.status_code == 200, resp.text
    assert resp.json()["matched"] == 1
    jobs = session.query(JobQueue).filter_by(job_type="refresh_metrics").all()
    assert [j.payload["content_id"] for j in jobs] == [video.id]


def test_bulk_refresh_explicit_unsupported_platform_returns_empty(client, session):
    from app.models import ContentItem, JobQueue

    video = ContentItem(account_id=1, platform="douyin", external_id="video", content_type="video")
    session.add(video)
    session.commit()

    resp = client.post("/api/assets/refresh-metrics/bulk", json={"platform": "wechat_mp"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"matched": 0, "job_ids": []}
    assert session.query(JobQueue).filter_by(job_type="refresh_metrics").count() == 0
