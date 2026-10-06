"""架构地基三表（creator_profile / raw_payload / task_log）的模型级测试。

覆盖：
- 三张表确实由模型元数据创建；
- CreatorProfile 的 (platform, author_id) 唯一约束经真实重复插入生效；
- RawPayload / TaskLog 能用默认值插入（JSON 默认 {}、字符串/数值/布尔默认）。
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models import CreatorProfile, RawPayload, TaskLog


def test_foundation_tables_exist(session):
    tables = set(inspect(session.get_bind()).get_table_names())
    assert {"creator_profile", "raw_payload", "task_log"} <= tables


def test_creator_profile_defaults(session):
    p = CreatorProfile(platform="douyin", author_id="u1")
    session.add(p)
    session.commit()

    assert p.id is not None
    assert p.author_name == ""
    assert p.avatar == ""
    assert p.bio == ""
    assert p.follower_count == 0
    assert p.following_count == 0
    assert p.verified is False
    assert p.homepage_url == ""
    assert p.last_synced_at is None
    assert p.raw == {}
    assert p.created_at is not None and p.updated_at is not None


def test_creator_profile_unique_platform_author(session):
    session.add(CreatorProfile(platform="douyin", author_id="u1"))
    session.commit()

    # 相同 (platform, author_id) 再次插入 → 唯一约束真实报错
    session.add(CreatorProfile(platform="douyin", author_id="u1"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # 任一维度不同则可共存
    session.add(CreatorProfile(platform="bilibili", author_id="u1"))
    session.add(CreatorProfile(platform="douyin", author_id="u2"))
    session.commit()
    assert session.query(CreatorProfile).count() == 3


def test_raw_payload_defaults(session):
    r = RawPayload(platform="xhs", source="crawl", external_id="note-1")
    session.add(r)
    session.commit()

    assert r.id is not None
    assert r.payload == {}
    assert r.content_id is None
    assert r.captured_at is not None


def test_task_log_defaults(session):
    t = TaskLog(job_type="download")
    session.add(t)
    session.commit()

    assert t.id is not None
    assert t.job_id is None
    assert t.level == "info"
    assert t.message == ""
    assert t.detail == {}
    assert t.actor == "worker"
    assert t.created_at is not None
