"""同步频率配置（需求 3，照抄抖小云）：随机时间 + 每次数量范围，存 system_setting。

- GET/PUT /api/system/sync-settings
- 区间纠正：min>max 自动交换；越界钳制；非法值 422
"""

from app.core.sync_settings import (
    get_sync_settings,
    pick_batch_size,
    pick_download_gap_seconds,
    pick_interval_minutes,
)


def test_defaults_when_unset(client):
    data = client.get("/api/system/sync-settings").json()
    assert data["interval_min_minutes"] <= data["interval_max_minutes"]
    assert data["batch_min"] <= data["batch_max"]
    assert data["download_gap_min_seconds"] <= data["download_gap_max_seconds"]
    # 默认贴近抖小云 BatchCount（≤30）
    assert data["batch_max"] <= 30


def test_update_roundtrip(client):
    r = client.put("/api/system/sync-settings", json={
        "interval_min_minutes": 60,
        "interval_max_minutes": 120,
        "batch_min": 5,
        "batch_max": 20,
        "download_gap_min_seconds": 2,
        "download_gap_max_seconds": 10,
    })
    assert r.status_code == 200, r.text
    data = client.get("/api/system/sync-settings").json()
    assert data["interval_min_minutes"] == 60 and data["interval_max_minutes"] == 120
    assert data["batch_min"] == 5 and data["batch_max"] == 20
    assert data["download_gap_min_seconds"] == 2.0 and data["download_gap_max_seconds"] == 10.0


def test_reversed_range_is_swapped(client):
    r = client.put("/api/system/sync-settings", json={
        "interval_min_minutes": 200, "interval_max_minutes": 100,
        "batch_min": 25, "batch_max": 5,
    })
    assert r.status_code == 200, r.text
    data = client.get("/api/system/sync-settings").json()
    assert data["interval_min_minutes"] == 100 and data["interval_max_minutes"] == 200
    assert data["batch_min"] == 5 and data["batch_max"] == 25


def test_batch_ceiling_is_30(client):
    # 超过抖小云上限 30 应被拒绝（422）
    r = client.put("/api/system/sync-settings", json={"batch_max": 99})
    assert r.status_code == 422
    # 合法上限 30 通过
    r = client.put("/api/system/sync-settings", json={"batch_max": 30})
    assert r.status_code == 200, r.text
    assert client.get("/api/system/sync-settings").json()["batch_max"] == 30


def test_invalid_type_rejected(client):
    assert client.put("/api/system/sync-settings", json={"batch_min": "abc"}).status_code == 422
    assert client.put("/api/system/sync-settings", json={"interval_min_minutes": True}).status_code == 422


def test_pickers_stay_within_configured_range(client):
    client.put("/api/system/sync-settings", json={
        "batch_min": 3, "batch_max": 7,
        "download_gap_min_seconds": 1, "download_gap_max_seconds": 2,
    })
    data = client.get("/api/system/sync-settings").json()
    for _ in range(50):
        assert 3 <= pick_batch_size(data) <= 7
        assert 1.0 <= pick_download_gap_seconds(data) <= 2.0
        assert data["interval_min_minutes"] <= pick_interval_minutes(data) <= data["interval_max_minutes"]
