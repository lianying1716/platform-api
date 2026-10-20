"""UI-01: asset list exposes a true filtered total and safe paging bounds."""

from datetime import datetime

from app.models import ContentItem, MediaAsset, PlatformAccount, StorageRoot


def _seed(session, count=7):
    root = StorageRoot(name="page-root", path="/tmp/xsai-pagination", is_default=True)
    account = PlatformAccount(platform="douyin", display_name="pager", external_id="pager-acct")
    session.add_all([root, account])
    session.flush()
    for index in range(count):
        content = ContentItem(
            account_id=account.id,
            platform="douyin" if index % 2 == 0 else "bilibili",
            external_id=f"page-{index}",
            content_type="video",
            title=f"asset-{index}",
            published_at=datetime(2026, 10, 1, 12, index),
        )
        session.add(content)
        session.flush()
        session.add(MediaAsset(content_id=content.id, storage_root_id=root.id, relative_path=f"r/{index}"))
    session.commit()


def test_total_counts_all_matching_rows_not_page_length(client, session):
    _seed(session, 7)
    first = client.get("/api/assets", params={"limit": 3, "offset": 0})
    assert first.status_code == 200
    assert len(first.json()) == 3
    assert first.headers["x-total-count"] == "7"

    last = client.get("/api/assets", params={"limit": 3, "offset": 6})
    assert len(last.json()) == 1
    assert last.headers["x-total-count"] == "7"


def test_total_respects_filters(client, session):
    _seed(session, 7)
    response = client.get("/api/assets", params={"platform": "bilibili"})
    assert response.headers["x-total-count"] == "3"
    assert all(row["platform"] == "bilibili" for row in response.json())


def test_offset_without_matches_is_empty_but_total_is_accurate(client, session):
    _seed(session, 2)
    response = client.get("/api/assets", params={"limit": 10, "offset": 50})
    assert response.json() == []
    assert response.headers["x-total-count"] == "2"


def test_invalid_paging_is_rejected_instead_of_silently_rewritten(client, session):
    _seed(session, 3)
    response = client.get("/api/assets", params={"limit": 0, "offset": -5})
    assert response.status_code == 422

    over_limit = client.get("/api/assets", params={"limit": 201})
    assert over_limit.status_code == 422


def test_export_contract_rejects_unknown_format_and_unbounded_batches(client, session):
    _seed(session, 1)
    bad_format = client.post("/api/assets/export", json={"asset_ids": [1], "format": "html"})
    assert bad_format.status_code == 422
    too_many = client.post(
        "/api/assets/export", json={"asset_ids": list(range(1, 502)), "format": "json"}
    )
    assert too_many.status_code == 422
