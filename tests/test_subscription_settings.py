"""订阅同步设置字段（迁移 0008）的模型级测试。

覆盖：
- subscription 表新增 6 列的列定义确实由模型元数据创建；
- 新建 Subscription 时 6 个字段落到设计默认值（0/0/50/True/"best"/False）；
- 显式赋值可覆盖默认（含非默认取值持久化）。
- 订阅 API（SubscriptionIn）范围校验：create/update 均 422。
"""

import pytest
from sqlalchemy import inspect

from app.models import PlatformAccount, Subscription


EXPECTED_DEFAULTS = {
    "min_play": 0,
    "publish_after_days": 0,
    "max_items": 50,
    "only_new": True,
    "download_quality": "best",
    "with_comments": False,
}


def _make_account(session) -> PlatformAccount:
    acc = PlatformAccount(platform="douyin", display_name="demo", status="active")
    session.add(acc)
    session.commit()
    return acc


def test_subscription_settings_columns_exist(session):
    cols = {c["name"] for c in inspect(session.get_bind()).get_columns("subscription")}
    assert set(EXPECTED_DEFAULTS) <= cols


def test_subscription_settings_defaults(session):
    acc = _make_account(session)
    sub = Subscription(account_id=acc.id, platform="douyin", source_type="favorite", remote_id="fav-1")
    session.add(sub)
    session.commit()
    session.refresh(sub)

    assert sub.min_play == 0
    assert sub.publish_after_days == 0
    assert sub.max_items == 50
    assert sub.only_new is True
    assert sub.download_quality == "best"
    assert sub.with_comments is False


def test_subscription_settings_explicit_values(session):
    acc = _make_account(session)
    sub = Subscription(
        account_id=acc.id,
        platform="bilibili",
        source_type="collection",
        remote_id="col-9",
        min_play=10000,
        publish_after_days=7,
        max_items=20,
        only_new=False,
        download_quality="1080",
        with_comments=True,
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)

    assert sub.min_play == 10000
    assert sub.publish_after_days == 7
    assert sub.max_items == 20
    assert sub.only_new is False
    assert sub.download_quality == "1080"
    assert sub.with_comments is True


# --- A4：SubscriptionIn 校验（create 与 update 共用同一模型） ---

_VALID_BODY = {"account_id": 1, "source_type": "favorite", "remote_id": "fav-1"}

_INVALID_CASES = [
    ("interval_minutes", 9),
    ("min_play", -1),
    ("max_items", 0),
    ("max_items", 501),
    ("publish_after_days", -1),
    ("download_quality", "4k"),
]


@pytest.mark.parametrize("field,value", _INVALID_CASES)
def test_create_subscription_rejects_invalid(client, field, value):
    r = client.post("/api/subscriptions", json={**_VALID_BODY, field: value})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("field,value", _INVALID_CASES)
def test_update_subscription_rejects_invalid(client, session, field, value):
    acc = _make_account(session)
    sub = Subscription(account_id=acc.id, platform="douyin", source_type="favorite", remote_id="fav-1")
    session.add(sub)
    session.commit()

    body = {**_VALID_BODY, "account_id": acc.id, field: value}
    r = client.put(f"/api/subscriptions/{sub.id}", json=body)
    assert r.status_code == 422, r.text


def test_create_subscription_accepts_valid_settings(client, session):
    acc = _make_account(session)
    body = {
        **_VALID_BODY,
        "account_id": acc.id,
        "interval_minutes": 10,
        "min_play": 0,
        "max_items": 500,
        "publish_after_days": 0,
        "download_quality": "audio",
        "only_new": False,
        "with_comments": True,
    }
    r = client.post("/api/subscriptions", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["download_quality"] == "audio"
    assert data["only_new"] is False
    assert data["with_comments"] is True
    assert data["max_items"] == 500


def test_list_subscriptions_serializes_last_sync_at(client, session):
    """回归：last_sync_at 为 datetime 时列表接口必须能序列化（此前 SubscriptionOut 声明为 str → 500）。"""
    from datetime import datetime

    acc = _make_account(session)
    sub = Subscription(account_id=acc.id, platform="douyin", source_type="favorite", remote_id="fav-1")
    session.add(sub)
    session.commit()
    sub.last_sync_at = datetime(2026, 10, 5, 23, 20, 0)
    session.commit()

    r = client.get("/api/subscriptions")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["last_sync_at"].startswith("2026-10-05T23:20:00")
