"""素材库列表排序（需求 4）：默认最新，另可选发布时间/同步时间/点赞数，全部倒序。

后端 /api/assets 的 sort 参数；like_count 取该内容最新一条指标快照。
"""

from datetime import datetime, timedelta

from app.models import (
    AccountCredential,
    ContentItem,
    ContentMetricSnapshot,
    MediaAsset,
    PlatformAccount,
)


def _mk(session, *, platform="douyin", ext, published, updated, like):
    acc = PlatformAccount(platform=platform, display_name="号", credential_kind="cookie", status="active")
    session.add(acc)
    session.flush()
    acc.credential = AccountCredential(ciphertext="x")
    content = ContentItem(
        account_id=acc.id, platform=platform, external_id=ext,
        content_type="video", title=f"t{ext}", published_at=published,
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id, download_status="done", updated_at=updated)
    session.add(asset)
    session.flush()
    session.add(ContentMetricSnapshot(content_id=content.id, captured_at=updated, play=1, like=like))
    session.commit()
    return asset.id


def test_sort_default_latest_and_others(client, session):
    base = datetime(2026, 1, 1, 12, 0, 0)
    # A：最早发布、最新同步、点赞中等
    a = _mk(session, ext="A", published=base, updated=base + timedelta(days=3), like=50)
    # B：最晚发布、最老同步、点赞最少
    b = _mk(session, ext="B", published=base + timedelta(days=2), updated=base, like=1)
    # C：中间发布、中间同步、点赞最多
    c = _mk(session, ext="C", published=base + timedelta(days=1), updated=base + timedelta(days=1), like=999)

    def ids(sort):
        rows = client.get("/api/assets", params={"sort": sort, "limit": 50}).json()
        return [r["asset_id"] for r in rows], {r["asset_id"]: r for r in rows}

    # 默认（latest）= 本次入库顺序（id 倒序）
    order, _ = ids("latest")
    assert order == [c, b, a]
    # 无 sort 参数时等同 latest
    assert [r["asset_id"] for r in client.get("/api/assets").json()] == [c, b, a]

    # 按发布时间倒序：B(最晚) -> C -> A
    order, _ = ids("published_at")
    assert order == [b, c, a]

    # 按同步时间倒序：A(最新) -> C -> B
    order, _ = ids("synced_at")
    assert order == [a, c, b]

    # 按点赞数倒序：C(最多) -> A -> B
    order, _ = ids("like_count")
    assert order == [c, a, b]


def test_sort_unknown_falls_back_to_latest(client, session):
    base = datetime(2026, 1, 1, 12, 0, 0)
    a = _mk(session, ext="A", published=base, updated=base, like=1)
    b = _mk(session, ext="B", published=base + timedelta(days=1), updated=base + timedelta(days=1), like=2)
    rows = client.get("/api/assets", params={"sort": "bogus"}).json()
    assert [r["asset_id"] for r in rows] == [b, a]
