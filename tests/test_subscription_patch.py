"""Subscription PATCH contract: partial updates must not rebuild the whole form."""

from app.models import PlatformAccount, StorageRoot, Subscription


def _seed(session):
    account = PlatformAccount(
        platform="douyin", display_name="主号", credential_kind="cookie", status="active"
    )
    session.add(account)
    session.flush()
    root = StorageRoot(name="NAS", path="/mnt/nas/xsai", is_default=False)
    session.add(root)
    session.flush()
    sub = Subscription(
        account_id=account.id,
        platform="douyin",
        source_type="favorite",
        remote_id="fav-1",
        title="收藏",
        enabled=True,
        storage_root_id=root.id,
        interval_minutes=45,
        auto_transcribe=False,
        auto_analyze=True,
        download_quality="1080",
        comment_mode="force",
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)
    session.refresh(root)
    return account, root, sub


def test_patch_toggle_preserves_custom_storage_and_overrides(client, session):
    account, root, sub = _seed(session)
    r = client.patch(f"/api/subscriptions/{sub.id}", json={"enabled": False})
    assert r.status_code == 200, r.text
    session.expire_all()
    row = session.get(Subscription, sub.id)
    assert row.enabled is False
    assert row.storage_root_id == root.id
    assert row.interval_minutes == 45
    assert row.auto_transcribe is False
    assert row.auto_analyze is True
    assert row.download_quality == "1080"
    assert row.comment_mode == "force"


def test_patch_can_explicitly_clear_storage_root(client, session):
    _account, _root, sub = _seed(session)
    r = client.patch(f"/api/subscriptions/{sub.id}", json={"storage_root_id": None})
    assert r.status_code == 200, r.text
    session.expire_all()
    assert session.get(Subscription, sub.id).storage_root_id is None


def test_patch_rejects_empty_and_unknown_fields(client, session):
    _account, _root, sub = _seed(session)
    assert client.patch(f"/api/subscriptions/{sub.id}", json={}).status_code == 422
    assert client.patch(f"/api/subscriptions/{sub.id}", json={"bogus": True}).status_code == 422


def test_patch_rejects_null_for_required_field(client, session):
    _account, _root, sub = _seed(session)
    r = client.patch(f"/api/subscriptions/{sub.id}", json={"enabled": None})
    assert r.status_code == 422, r.text


def test_patch_rejects_missing_storage_root(client, session):
    _account, _root, sub = _seed(session)
    r = client.patch(f"/api/subscriptions/{sub.id}", json={"storage_root_id": 999999})
    assert r.status_code == 404, r.text


def test_patch_validates_quality_and_source_type(client, session):
    _account, _root, sub = _seed(session)
    assert client.patch(f"/api/subscriptions/{sub.id}", json={"download_quality": "4k"}).status_code == 422
    assert client.patch(f"/api/subscriptions/{sub.id}", json={"source_type": "watch_later"}).status_code == 422


def test_patch_moves_account_only_within_same_platform(client, session):
    _account, _root, sub = _seed(session)
    other = PlatformAccount(platform="douyin", display_name="小号", status="active")
    mismatch = PlatformAccount(platform="bilibili", display_name="B站", status="active")
    session.add_all([other, mismatch])
    session.commit()

    r = client.patch(f"/api/subscriptions/{sub.id}", json={"account_id": mismatch.id})
    assert r.status_code == 422, r.text
    r = client.patch(f"/api/subscriptions/{sub.id}", json={"account_id": other.id})
    assert r.status_code == 200, r.text
    session.expire_all()
    assert session.get(Subscription, sub.id).account_id == other.id


def test_sync_switch_capabilities_expose_only_real_sources(client):
    data = client.get("/api/sync-switches/capabilities")
    assert data.status_code == 200, data.text
    assert data.json()["douyin"] == ["favorite", "creator"]
    assert data.json()["bilibili"] == ["watch_later", "creator"]
    assert data.json()["xhs"] == ["creator"]
