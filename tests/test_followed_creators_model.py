"""followed_creator 表：列/默认值/唯一约束。"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import FollowedCreator, PlatformAccount


def _account(session) -> PlatformAccount:
    acc = PlatformAccount(platform="bilibili", display_name="测试号")
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


def test_defaults(session):
    acc = _account(session)
    fc = FollowedCreator(
        account_id=acc.id, platform="bilibili", author_id="1001", author_name="张三"
    )
    session.add(fc)
    session.commit()
    session.refresh(fc)
    assert fc.sync_enabled is False
    assert fc.full_sync is False
    assert fc.follower_count == 0
    assert fc.like_count == 0
    assert fc.avatar == ""
    assert fc.verified is False
    assert fc.raw == {}


def test_unique_account_author(session):
    acc = _account(session)
    session.add(FollowedCreator(account_id=acc.id, platform="bilibili", author_id="1001"))
    session.commit()
    session.add(FollowedCreator(account_id=acc.id, platform="bilibili", author_id="1001"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_same_author_different_account_ok(session):
    a1 = _account(session)
    a2 = PlatformAccount(platform="bilibili", display_name="第二个号")
    session.add(a2)
    session.commit()
    session.add(FollowedCreator(account_id=a1.id, platform="bilibili", author_id="1001"))
    session.add(FollowedCreator(account_id=a2.id, platform="bilibili", author_id="1001"))
    session.commit()
