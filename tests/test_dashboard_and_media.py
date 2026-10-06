"""总览统计扩展 + 素材库卡片字段 + 视频 Range 流式播放。"""

import os
from datetime import datetime

from app.models import ContentItem, ContentMetricSnapshot, MediaAsset, StorageRoot


def _seed(session, *, content_type="video", video_file="", relative_path="", size=0):
    content = ContentItem(
        platform="douyin",
        external_id=f"e-{content_type}-{video_file or 'x'}",
        content_type=content_type,
        title="示例内容",
        author_name="老饭骨",
        author_id="a1",
        url="https://example.com/v/1",
        published_at=datetime(2026, 9, 1, 10, 0, 0),
        duration_sec=192,
        auto_category="food",
        category_confidence=0.82,
        source_kind="subscription",
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(
        content_id=content.id,
        relative_path=relative_path,
        video_file=video_file,
        download_status="done",
        transcribe_status="done",
        understand_status="done",
        process_tier="advanced",
    )
    session.add(asset)
    session.flush()
    session.add(
        ContentMetricSnapshot(
            content_id=content.id, play=12000, like=800, comment=90, favorite=40, share=5
        )
    )
    session.commit()
    return content, asset


def test_assets_list_returns_card_fields(client, session):
    _seed(session, content_type="video", relative_path="douyin/x/2026-09-01/v1")
    r = client.get("/api/assets")
    assert r.status_code == 200, r.text
    row = r.json()[0]
    for field in (
        "content_type",
        "auto_category",
        "category_confidence",
        "published_at",
        "created_at",
        "synced_at",
        "duration_sec",
        "size_bytes",
        "playable",
        "tier",
    ):
        assert field in row, f"missing field {field}"
    assert row["content_type"] == "video"
    assert row["auto_category"] == "food"
    assert row["duration_sec"] == 192
    assert row["metrics"]["favorite"] == 40


def test_assets_list_filters_by_content_type_and_category(client, session):
    _seed(session, content_type="video", relative_path="d/a")
    _seed(session, content_type="article", relative_path="d/b")
    assert len(client.get("/api/assets", params={"content_type": "article"}).json()) == 1
    assert len(client.get("/api/assets", params={"category": "food"}).json()) == 2


def test_article_endpoint_returns_text(client, session):
    _seed(session, content_type="article", relative_path="wechat_mp/x")
    asset_id = session.query(MediaAsset).first().id
    body = client.get(f"/api/assets/{asset_id}/article").json()
    assert body["platform"] == "douyin"
    assert "title" in body and "text" in body


def test_media_range_returns_206(client, session, tmp_path):
    payload = bytes(range(256)) * 4  # 1024 bytes
    root = StorageRoot(name="t", path=str(tmp_path), is_default=True)
    session.add(root)
    session.flush()
    rel = "douyin/x/2026-09-01"
    os.makedirs(tmp_path / rel, exist_ok=True)
    (tmp_path / rel / "v.mp4").write_bytes(payload)
    content = ContentItem(
        platform="douyin", external_id="m1", content_type="video", title="t", author_id="a1"
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(
        content_id=content.id,
        storage_root_id=root.id,
        relative_path=rel,
        video_file="v.mp4",
        download_status="done",
    )
    session.add(asset)
    session.commit()

    r = client.get(f"/api/assets/{asset.id}/media", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206, r.text
    assert r.headers["content-range"] == f"bytes 0-99/{len(payload)}"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.content == payload[:100]

    full = client.get(f"/api/assets/{asset.id}/media")
    assert full.status_code == 200
    assert full.content == payload


def test_media_missing_file_returns_404(client, session):
    _seed(session, content_type="video", relative_path="douyin/none")
    asset_id = session.query(MediaAsset).first().id
    r = client.get(f"/api/assets/{asset_id}/media")
    assert r.status_code == 404


def test_dashboard_summary_has_new_sections(client, session):
    _seed(session, content_type="video", relative_path="d/a")
    _seed(session, content_type="article", relative_path="d/b")
    body = client.get("/api/dashboard/summary").json()
    assert body["content"]["video"] == 1
    assert body["content"]["article"] == 1
    assert body["content"]["total"] == 2
    assert body["content"]["duration_sec"] == 192
    assert body["platforms"][0]["platform"] == "douyin"
    assert body["platforms"][0]["play"] == 24000
    assert {c["key"] for c in body["categories"]} == {"food"}
    assert body["engagement"]["play"] == 24000
    assert body["processing"]["transcribed"] == 2
    assert "storage" in body and "used_bytes" in body["storage"]
    assert len(body["trend"]) == 14
    assert body["crawled_today"] == body["today_crawled"]
    assert body["accounts_error"] == body["accounts"]["abnormal"]
