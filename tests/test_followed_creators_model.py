"""关注关系模型：一条 creator 订阅就是"我关注了谁"，状态只有一份。

旧的 followed_creator 表已删除——它与 creator 订阅 1:1 重复，
且两边各存一套开关，改一边另一边不跟着变（状态漂移）。
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import PlatformAccount, Subscription


def _account(session) -> PlatformAccount:
    acc = PlatformAccount(platform="bilibili", display_name="测试号")
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


def test_creator_subscription_defaults(session):
    acc = _account(session)
    sub = Subscription(
        account_id=acc.id, platform="bilibili", source_type="creator", remote_id="1001",
        title="张三",
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)
    assert sub.enabled is True           # 显式跟踪即视为启用
    assert sub.only_new is True            # 默认只同步新发布
    assert sub.last_sync_at is None
    assert sub.title == "张三"


def test_unique_account_source_remote(session):
    """同一账号下同一来源只能有一条追踪记录（从数据层杜绝重复行）。"""
    acc = _account(session)
    session.add(Subscription(account_id=acc.id, platform="bilibili",
                             source_type="creator", remote_id="1001"))
    session.commit()
    session.add(Subscription(account_id=acc.id, platform="bilibili",
                             source_type="creator", remote_id="1001"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_same_remote_different_source_type_ok(session):
    """同一 id 既是收藏夹又是博主时互不冲突（remote_id 语义随 source_type 变）。"""
    acc = _account(session)
    session.add(Subscription(account_id=acc.id, platform="bilibili",
                             source_type="creator", remote_id="1001"))
    session.add(Subscription(account_id=acc.id, platform="bilibili",
                             source_type="favorite", remote_id="1001"))
    session.commit()
    assert session.query(Subscription).count() == 2


def test_same_creator_different_account_ok(session):
    a1 = _account(session)
    a2 = PlatformAccount(platform="bilibili", display_name="第二个号")
    session.add(a2)
    session.commit()
    session.add(Subscription(account_id=a1.id, platform="bilibili",
                             source_type="creator", remote_id="1001"))
    session.add(Subscription(account_id=a2.id, platform="bilibili",
                             source_type="creator", remote_id="1001"))
    session.commit()
    assert session.query(Subscription).count() == 2


def test_followed_creator_table_is_gone():
    """回归防护：不要有人把重复的第二张表加回来。"""
    import app.models as m

    assert not hasattr(m, "FollowedCreator")
