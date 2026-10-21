"""Unknown metrics must not be collapsed into measured zeroes."""

from datetime import datetime

from app.domain.metrics import normalize_metrics
from app.models import AccountCredential, ContentItem, ContentMetricSnapshot, MediaAsset, PlatformAccount


def test_normalizer_distinguishes_missing_from_zero():
    assert normalize_metrics({"play": 0, "like": "5"}) == {
        "play": 0,
        "like": 5,
        "comment": None,
        "favorite": None,
        "share": None,
    }
    assert normalize_metrics({}) == {
        "play": None,
        "like": None,
        "comment": None,
        "favorite": None,
        "share": None,
    }


def test_sync_records_unknown_not_zero(session):
    from app.services.sync_service import _record_metric

    account = PlatformAccount(platform="douyin", display_name="a", credential_kind="cookie", status="active")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="douyin",
        external_id="metric-unknown",
        content_type="video",
        title="unknown",
    )
    session.add(content)
    session.flush()
    _record_metric(
        session,
        content,
        {"metrics": {"like": 10, "comment": 2, "favorite": 3, "share": 1}},
    )
    session.commit()
    row = session.query(ContentMetricSnapshot).one()
    assert row.play is None
    assert row.like == 10


def test_dashboard_hot_excludes_unknown_and_keeps_real_zero(client, session):
    account = PlatformAccount(platform="douyin", display_name="a", credential_kind="cookie", status="active")
    session.add(account)
    session.flush()
    zero = ContentItem(
        account_id=account.id,
        platform="douyin",
        external_id="zero-play",
        content_type="video",
        title="真实零播放",
    )
    unknown = ContentItem(
        account_id=account.id,
        platform="douyin",
        external_id="unknown-play",
        content_type="video",
        title="播放未知",
    )
    session.add_all([zero, unknown])
    session.flush()
    session.add_all(
        [
            MediaAsset(content_id=zero.id, download_status="done"),
            MediaAsset(content_id=unknown.id, download_status="done"),
        ]
    )
    session.add(ContentMetricSnapshot(content_id=zero.id, play=0, like=1, captured_at=datetime.utcnow()))
    session.add(ContentMetricSnapshot(content_id=unknown.id, play=None, like=1, captured_at=datetime.utcnow()))
    session.commit()

    body = client.get("/api/dashboard/summary").json()
    assert [row["title"] for row in body["hot"]] == ["真实零播放"]

    rows = client.get("/api/assets").json()
    by_title = {row["title"]: row for row in rows}
    assert by_title["真实零播放"]["metrics"]["play"] == 0
    assert by_title["播放未知"]["metrics"]["play"] is None


def test_dashboard_engagement_stays_unknown_when_every_play_is_missing(client, session):
    account = PlatformAccount(platform="douyin", display_name="unknown", credential_kind="cookie", status="active")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="douyin",
        external_id="all-play-unknown",
        content_type="video",
        title="播放全部缺失",
    )
    session.add(content)
    session.flush()
    session.add(MediaAsset(content_id=content.id, download_status="done"))
    session.add(
        ContentMetricSnapshot(
            content_id=content.id,
            play=None,
            like=10,
            comment=2,
            favorite=3,
            share=1,
            captured_at=datetime.utcnow(),
        )
    )
    session.commit()

    body = client.get("/api/dashboard/summary").json()
    assert body["engagement"]["play"] is None
    assert body["engagement"]["like"] == 10
    platform = next(p for p in body["platforms"] if p["platform"] == "douyin")
    assert platform["play"] is None


def test_dashboard_platform_without_snapshot_has_unknown_metrics(client, session):
    account = PlatformAccount(platform="douyin", display_name="nosnap", credential_kind="cookie", status="active")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="douyin",
        external_id="no-snapshot",
        content_type="video",
        title="没有快照",
    )
    session.add(content)
    session.commit()

    body = client.get("/api/dashboard/summary").json()
    platform = next(p for p in body["platforms"] if p["platform"] == "douyin")
    assert platform["play"] is None
    assert platform["like"] is None
    assert body["engagement"]["play"] is None


def test_dashboard_counts_one_snapshot_when_timestamps_tie(client, session):
    account = PlatformAccount(platform="douyin", display_name="ties", credential_kind="cookie", status="active")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id,
        platform="douyin",
        external_id="tie",
        content_type="video",
        title="同一时刻两条快照",
    )
    session.add(content)
    session.flush()
    captured = datetime(2026, 10, 21, 12, 0, 0)
    session.add_all(
        [
            ContentMetricSnapshot(content_id=content.id, play=10, like=1, captured_at=captured),
            ContentMetricSnapshot(content_id=content.id, play=99, like=9, captured_at=captured),
        ]
    )
    session.commit()

    body = client.get("/api/dashboard/summary").json()
    assert body["engagement"]["play"] == 99
    assert body["hot"][0]["play"] == 99


def test_dashboard_coverage_reports_partial_unknown_without_fake_total(client, session):
    account = PlatformAccount(platform="douyin", display_name="partial", credential_kind="cookie", status="active")
    session.add(account)
    session.flush()
    known = ContentItem(
        account_id=account.id, platform="douyin", external_id="known", content_type="video", title="已知"
    )
    missing = ContentItem(
        account_id=account.id, platform="douyin", external_id="missing", content_type="video", title="未知"
    )
    session.add_all([known, missing])
    session.flush()
    now = datetime(2026, 10, 21, 12, 0, 0)
    session.add_all(
        [
            ContentMetricSnapshot(content_id=known.id, play=40, like=4, captured_at=now),
            ContentMetricSnapshot(content_id=missing.id, play=None, like=6, captured_at=now),
        ]
    )
    session.commit()

    body = client.get("/api/dashboard/summary").json()
    assert body["engagement"]["play"] == 40
    assert body["metric_coverage"]["play"] == {"known": 1, "total": 2}
    assert body["metric_coverage"]["like"] == {"known": 2, "total": 2}
    platform = next(p for p in body["platforms"] if p["platform"] == "douyin")
    assert platform["metric_coverage"]["play"] == {"known": 1, "total": 2}


def test_dashboard_hot_uses_latest_snapshot_not_historical_maximum(client, session):
    account = PlatformAccount(platform="douyin", display_name="latest", credential_kind="cookie", status="active")
    session.add(account)
    session.flush()
    content = ContentItem(
        account_id=account.id, platform="douyin", external_id="history", content_type="video", title="历史峰值"
    )
    session.add(content)
    session.flush()
    now = datetime.utcnow()
    session.add_all(
        [
            ContentMetricSnapshot(content_id=content.id, play=100000, captured_at=now),
            ContentMetricSnapshot(content_id=content.id, play=12, captured_at=now),
        ]
    )
    session.commit()

    body = client.get("/api/dashboard/summary").json()
    assert body["hot"][0]["play"] == 12


def test_dashboard_coverage_excludes_metrics_that_cannot_apply(client, session):
    """Articles must not make video-only play coverage look permanently broken."""
    account = PlatformAccount(platform="douyin", display_name="scope", credential_kind="cookie", status="active")
    session.add(account)
    session.flush()
    video = ContentItem(
        account_id=account.id, platform="douyin", external_id="video", content_type="video"
    )
    article = ContentItem(
        account_id=account.id, platform="douyin", external_id="article", content_type="article"
    )
    session.add_all([video, article])
    session.flush()
    # Deliberately include a stray play value on the article.  It must not
    # enter the video-only numerator or inflate the per-platform play sum.
    session.add(ContentMetricSnapshot(content_id=article.id, like=5, play=999))
    session.commit()

    body = client.get("/api/dashboard/summary").json()
    assert body["metric_coverage"]["play"] == {"known": 0, "total": 1}
    assert body["metric_coverage"]["like"] == {"known": 1, "total": 2}
    douyin = next(p for p in body["platforms"] if p["platform"] == "douyin")
    assert douyin["metric_coverage"]["play"] == {"known": 0, "total": 1}
    assert douyin["metric_coverage"]["like"] == {"known": 1, "total": 2}
    assert douyin["play"] is None
    assert body["engagement"]["play"] is None


def test_dashboard_hot_excludes_non_video_play_values(client, session):
    account = PlatformAccount(platform="douyin", display_name="hot", credential_kind="cookie", status="active")
    session.add(account)
    session.flush()
    video = ContentItem(
        account_id=account.id, platform="douyin", external_id="video", content_type="video", title="视频"
    )
    article = ContentItem(
        account_id=account.id, platform="douyin", external_id="article", content_type="article", title="文章"
    )
    session.add_all([video, article])
    session.flush()
    session.add_all(
        [
            ContentMetricSnapshot(content_id=video.id, play=10),
            ContentMetricSnapshot(content_id=article.id, play=999),
        ]
    )
    session.commit()

    body = client.get("/api/dashboard/summary").json()
    assert [row["title"] for row in body["hot"]] == ["视频"]
