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
