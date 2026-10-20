"""阶段 3：账号/订阅变动不误删素材；删除必须显式且留审计。"""

from sqlalchemy import select

from app.core.crypto import encrypt_credential
from app.models import (
    AccountCredential,
    ContentItem,
    ContentSource,
    DeletionAudit,
    MediaAsset,
    PlatformAccount,
    Subscription,
)


def _account(session, platform="bilibili", name="A"):
    account = PlatformAccount(platform=platform, display_name=name, status="active", credential_kind="cookie")
    session.add(account)
    session.flush()
    account.credential = AccountCredential(ciphertext=encrypt_credential({"cookie": "c=1"}))
    session.commit()
    return account


def _content_with_asset(session, account, external_id="V1"):
    content = ContentItem(
        account_id=account.id,
        platform=account.platform,
        external_id=external_id,
        content_type="video",
        title=external_id,
    )
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id, download_status="done")
    session.add(asset)
    session.commit()
    return content, asset


def test_delete_account_is_soft_and_keeps_assets(client, session):
    """删账号只软删除，素材/内容/来源都保留。"""
    account = _account(session)
    content, asset = _content_with_asset(session, account)
    session.add(
        ContentSource(
            content_id=content.id,
            account_id=account.id,
            link_kind="subscription",
            source_ref="",
            active=True,
            last_seen_at=None,
        )
    )
    session.commit()

    resp = client.delete(f"/api/accounts/{account.id}")

    assert resp.status_code == 200
    session.refresh(account)
    assert account.status == "deleted"
    assert session.get(ContentItem, content.id) is not None
    assert session.get(MediaAsset, asset.id) is not None
    assert session.scalar(select(ContentSource).where(ContentSource.content_id == content.id)) is not None


def test_delete_subscription_deactivates_source_and_keeps_assets(client, session):
    """删订阅只停用来源，不删除内容与素材。"""
    account = _account(session)
    content, asset = _content_with_asset(session, account)
    sub = Subscription(
        account_id=account.id,
        platform=account.platform,
        source_type="favorite",
        remote_id="fav-1",
        title="收藏夹",
    )
    session.add(sub)
    session.flush()
    source = ContentSource(
        content_id=content.id,
        account_id=account.id,
        subscription_id=sub.id,
        link_kind="subscription",
        source_ref="",
        active=True,
        last_seen_at=None,
    )
    session.add(source)
    session.commit()

    resp = client.delete(f"/api/subscriptions/{sub.id}")

    assert resp.status_code == 200
    session.refresh(source)
    assert source.active is False
    assert session.get(ContentItem, content.id) is not None
    assert session.get(MediaAsset, asset.id) is not None


def test_agent_delete_requires_confirm_and_writes_audit(client, session):
    """外部 Agent 删除素材必须 confirm=true，且写删除审计。"""
    account = _account(session)
    content, asset = _content_with_asset(session, account, "V-DEL")
    asset_id = asset.id
    created = client.post("/api/ai/keys", json={"name": "agent", "scopes": ["operate"]})
    assert created.status_code == 200, created.text
    headers = {"Authorization": f"Bearer {created.json()['api_key']}"}

    denied = client.post(f"/api/skills/assets/{asset_id}/delete", json={}, headers=headers)
    assert denied.status_code in (400, 422)
    assert session.get(MediaAsset, asset_id) is not None

    ok = client.post(
        f"/api/skills/assets/{asset_id}/delete",
        json={"confirm": True, "reason": "重复素材"},
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json()["job_id"] > 0
    session.expire_all()
    asset = session.get(MediaAsset, asset_id)
    assert asset is not None and asset.deleted_at is not None, "删除先软删除，worker 清完文件再物理删"
    audit = session.scalar(select(DeletionAudit).where(DeletionAudit.asset_id == asset_id))
    assert audit is not None and audit.reason == "重复素材" and audit.status == "pending"
