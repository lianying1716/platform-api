"""订阅同步设置字段（迁移 0008）的模型级测试。

覆盖：
- subscription 表新增 6 列的列定义确实由模型元数据创建；
- 新建 Subscription 时 6 个字段落到设计默认值（0/0/50/True/"best"/False）；
- 显式赋值可覆盖默认（含非默认取值持久化）。
"""

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
    acc = PlatformAccount(platform="douyin", display_name="demo")
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
