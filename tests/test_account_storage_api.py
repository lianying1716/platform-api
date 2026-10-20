"""账号存储根 API + 订阅自动加工开关 API 的端到端校验。"""

from app.models import PlatformAccount, StorageRoot


def _mk_root(session, name, path, *, is_default=False):
    r = StorageRoot(name=name, path=path, is_default=is_default)
    session.add(r)
    session.commit()
    return r


def _mk_account(session, platform="bilibili", name="主号"):
    acc = PlatformAccount(platform=platform, display_name=name, status="active")
    session.add(acc)
    session.commit()
    return acc


def test_account_storage_update_and_readback(client, session):
    root = _mk_root(session, "NAS", "/mnt/nas/xsai")
    acc = _mk_account(session)
    acc_id = acc.id

    # 指定根
    r2 = client.put(f"/api/accounts/{acc_id}/storage", json={"storage_root_id": root.id})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["storage_root_id"] == root.id
    assert body["storage_root_name"] == "NAS"
    assert body["storage_root_path"] == "/mnt/nas/xsai"

    # 列表里也带上存储信息
    listed = client.get("/api/accounts").json()
    row = next(a for a in listed if a["id"] == acc_id)
    assert row["storage_root_id"] == root.id

    # 清除回默认
    r3 = client.put(f"/api/accounts/{acc_id}/storage", json={"storage_root_id": None})
    assert r3.status_code == 200
    assert r3.json()["storage_root_id"] is None


def test_account_storage_rejects_unknown_root(client, session):
    acc = _mk_account(session, name="x")
    bad = client.put(f"/api/accounts/{acc.id}/storage", json={"storage_root_id": 999999})
    assert bad.status_code == 404


def test_subscription_auto_flags_roundtrip(client, session):
    acc = PlatformAccount(platform="douyin", display_name="a", status="active")
    session.add(acc)
    session.commit()
    payload = {
        "account_id": acc.id,
        "source_type": "creator",
        "remote_id": "r1",
        "auto_transcribe": True,
        "auto_analyze": True,
    }
    created = client.post("/api/subscriptions", json=payload)
    assert created.status_code == 200, created.text
    sub = created.json()
    assert sub["auto_transcribe"] is True and sub["auto_analyze"] is True
    assert "{account}" in sub["subdir_template"]

    updated = client.put(
        f"/api/subscriptions/{sub['id']}",
        json={**payload, "auto_transcribe": False, "auto_analyze": False},
    )
    assert updated.status_code == 200
    assert updated.json()["auto_transcribe"] is False
    assert updated.json()["auto_analyze"] is False


def test_process_settings_auto_flags_roundtrip(client, session):
    """系统设置里的全局自动加工开关可读写（KV 往返）。"""
    base = client.get("/api/system/process-settings").json()
    assert base["auto_transcribe"] is False and base["auto_analyze"] is False

    put = client.put(
        "/api/system/process-settings",
        json={**{k: base[k] for k in (
            "min_heat", "min_likes_floor", "budget", "top_pct", "weight_like",
            "weight_comment", "weight_play", "video_max_mb", "cover_max_mb",
            "low_confidence_threshold", "classify_enabled", "use_cover",
        )}, "auto_transcribe": True, "auto_analyze": True},
    )
    assert put.status_code == 200, put.text
    after = client.get("/api/system/process-settings").json()
    assert after["auto_transcribe"] is True and after["auto_analyze"] is True
