"""OBS-04: asset lists must not issue one query per row."""

import time
from datetime import datetime

from sqlalchemy import event, insert, text

from app.api import assets as assets_api
from app.models import ContentItem, ContentMetricSnapshot, MediaAsset, StorageRoot


def test_asset_list_query_count_is_bounded_on_10k_rows(client, session, tmp_path, monkeypatch):
    root = StorageRoot(name="perf", path=str(tmp_path), is_default=True)
    session.add(root)
    session.flush()
    now = datetime.utcnow()
    session.execute(
        insert(ContentItem),
        [
            {
                "account_id": 1,
                "platform": "douyin",
                "external_id": f"perf-{i}",
                "content_type": "video",
                "title": f"perf {i}",
                "author_name": "author",
                "author_id": "a1",
                "url": f"https://example.test/{i}",
                "published_at": now,
                "created_at": now,
            }
            for i in range(10_000)
        ],
    )
    session.commit()
    ids = [row[0] for row in session.execute(text("select id from content_item order by id")).all()]
    session.execute(
        insert(MediaAsset),
        [
            {
                "content_id": cid,
                "storage_root_id": root.id,
                "relative_path": f"perf/{cid}",
                "video_file": "v.mp4",
                "download_status": "done",
                "created_at": now,
                "updated_at": now,
            }
            for cid in ids
        ],
    )
    session.execute(
        insert(ContentMetricSnapshot),
        [
            {"content_id": cid, "play": cid, "like": cid, "comment": 1, "captured_at": now}
            for cid in ids
        ],
    )
    session.commit()
    # The API fixture has its own session; seed through the shared in-memory DB.
    monkeypatch.setattr(assets_api.storage, "sizes_for_assets", lambda _db, rows: {a.id: 0 for a in rows})

    statements: list[str] = []
    def before_execute(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", before_execute)
    try:
        started = time.perf_counter()
        response = client.get("/api/assets", params={"limit": 200})
        elapsed = time.perf_counter() - started
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", before_execute)
    assert response.status_code == 200
    assert len(response.json()) == 200
    # One page query, one author query, one metric query and a bounded storage
    # lookup. The old implementation had one metric query per row.
    assert len(statements) < 20, f"asset list issued {len(statements)} SQL statements"
    assert elapsed < 5.0


def test_dashboard_does_not_materialise_all_asset_rows(client, session, tmp_path, monkeypatch):
    from app.core import storage

    root = StorageRoot(name="dash-perf", path=str(tmp_path), is_default=True)
    session.add(root)
    session.flush()
    now = datetime.utcnow()
    session.execute(
        insert(ContentItem),
        [
            {
                "account_id": 1,
                "platform": "douyin",
                "external_id": f"dash-{i}",
                "content_type": "video",
                "title": f"dash {i}",
                "created_at": now,
            }
            for i in range(10_000)
        ],
    )
    session.commit()
    ids = [row[0] for row in session.execute(text("select id from content_item order by id")).all()]
    session.execute(
        insert(MediaAsset),
        [
            {
                "content_id": cid,
                "storage_root_id": root.id,
                "relative_path": f"dash/{cid}",
                "video_file": "v.mp4",
                "download_status": "done",
                "created_at": now,
                "updated_at": now,
            }
            for cid in ids
        ],
    )
    session.commit()
    monkeypatch.setattr(storage, "sizes_for_asset_rows", lambda _db, rows: {r[0]: 10 for r in rows})
    monkeypatch.setattr(storage, "cached_root_file_sizes", lambda _path: {})
    response = client.get("/api/dashboard/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["assets_total"] == 10_000
    assert body["storage"]["by_platform"]["douyin"] == 100_000
