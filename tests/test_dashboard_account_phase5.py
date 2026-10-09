"""阶段 5：看板支持账号维度，账号视图只统计该账号的素材。"""

from app.core.crypto import encrypt_credential
from app.models import (
    AccountCredential,
    ContentItem,
    ContentMetricSnapshot,
    MediaAsset,
    PlatformAccount,
)


def _account(session, name):
    account = PlatformAccount(platform="bilibili", display_name=name, status="active", credential_kind="cookie")
    session.add(account)
    session.flush()
    account.credential = AccountCredential(ciphertext=encrypt_credential({"cookie": "c=1"}))
    return account


def _seed(session, account, external_id, play):
    content = ContentItem(
        account_id=account.id,
        platform="bilibili",
        external_id=external_id,
        content_type="video",
        title=external_id,
        duration_sec=60,
    )
    session.add(content)
    session.flush()
    session.add(MediaAsset(content_id=content.id, download_status="done"))
    session.add(ContentMetricSnapshot(content_id=content.id, play=play, like=play // 10))
    session.commit()


def test_dashboard_summary_filters_by_account(client, session):
    a = _account(session, "A")
    b = _account(session, "B")
    _seed(session, a, "A-1", 1000)
    _seed(session, b, "B-1", 9000)
    _seed(session, b, "B-2", 8000)

    all_data = client.get("/api/dashboard/summary").json()
    assert all_data["content"]["total"] == 3

    a_data = client.get("/api/dashboard/summary", params={"account_id": a.id}).json()
    assert a_data["content"]["total"] == 1
    assert a_data["assets_total"] == 1
    assert a_data["engagement"]["play"] == 1000
    assert all(p["total"] == 1 for p in a_data["platforms"])

    b_data = client.get("/api/dashboard/summary", params={"account_id": b.id}).json()
    assert b_data["content"]["total"] == 2
    assert b_data["engagement"]["play"] == 17000


def test_dashboard_trend_respects_account_filter(client, session):
    a = _account(session, "A")
    b = _account(session, "B")
    _seed(session, a, "A-1", 100)
    _seed(session, b, "B-1", 200)

    a_data = client.get("/api/dashboard/summary", params={"account_id": a.id}).json()
    assert sum(d["added"] for d in a_data["trend"]) == 1
