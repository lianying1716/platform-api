"""把「关注博主」合并进「订阅源」：一张表管理我跟踪的所有来源

问题：followed_creator.sync_enabled/full_sync 与 subscription.enabled/only_new
是同一件事的两份状态，两边都能独立写 → 状态漂移（在订阅源里禁用/删除订阅，
关注卡片仍显示"开启同步"）。同一个博主还会同时出现在两个 tab。

做法：
1. 先把 followed_creator 的数据搬进 subscription（source_type='creator'），
   已存在同 (account_id, source_type, remote_id) 的行则合并开关状态；资料进 creator_profile。
2. 给 subscription 加唯一约束 (account_id, source_type, remote_id)，从数据层面杜绝重复行。
3. 删掉 followed_creator 表。

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 1) 迁移 followed_creator → subscription + creator_profile ----
    rows = bind.execute(
        sa.text(
            """
            SELECT fc.id, fc.account_id, fc.platform, fc.author_id, fc.author_name,
                   fc.avatar, fc.home_url, fc.signature, fc.verified,
                   fc.follower_count, fc.sync_enabled, fc.full_sync, fc.last_synced_at
            FROM followed_creator fc
            """
        )
    ).mappings().all()

    for r in rows:
        # 已有同来源的 creator 订阅？有就合并开关（任一为开即视为开），没有就插入
        existing = bind.execute(
            sa.text(
                """
                SELECT id, enabled FROM subscription
                WHERE account_id = :acc AND source_type = 'creator' AND remote_id = :rid
                """
            ),
            {"acc": r["account_id"], "rid": r["author_id"]},
        ).mappings().first()

        if existing is None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO subscription
                        (account_id, platform, source_type, remote_id, title, enabled,
                         auto_download, interval_minutes, subdir_template,
                         min_play, publish_after_days, max_items, only_new,
                         download_quality, with_comments, last_sync_at, created_at)
                    VALUES
                        (:acc, :plat, 'creator', :rid, :title, :enabled,
                         false, 360, '{platform}/{creator}/{date}',
                         0, 0, 50, :only_new,
                         'best', false, :last_sync, now())
                    """
                ),
                {
                    "acc": r["account_id"],
                    "plat": r["platform"],
                    "rid": r["author_id"],
                    "title": r["author_name"] or r["author_id"],
                    "enabled": bool(r["sync_enabled"]),
                    "only_new": not bool(r["full_sync"]),
                    "last_sync": r["last_synced_at"],
                },
            )
        else:
            # 保留两边中更"开启"的状态，时间取较新的，避免迁移丢失用户意图
            bind.execute(
                sa.text(
                    """
                    UPDATE subscription
                       SET enabled = (enabled OR :enabled),
                           last_sync_at = COALESCE(:last_sync, last_sync_at)
                     WHERE id = :sid
                    """
                ),
                {"enabled": bool(r["sync_enabled"]), "last_sync": r["last_synced_at"], "sid": existing["id"]},
            )

        # 博主资料进 creator_profile（只补空字段，避免覆盖更权威的值）
        prof = bind.execute(
            sa.text(
                "SELECT id FROM creator_profile WHERE platform = :plat AND author_id = :rid"
            ),
            {"plat": r["platform"], "rid": r["author_id"]},
        ).first()
        if prof is None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO creator_profile
                        (platform, author_id, author_name, avatar, bio, follower_count,
                         following_count, verified, homepage_url, last_synced_at, raw,
                         created_at, updated_at)
                    VALUES
                        (:plat, :rid, :name, :avatar, :bio, :followers,
                         0, :verified, :home, :last_sync, '{}'::json,
                         now(), now())
                    """
                ),
                {
                    "plat": r["platform"], "rid": r["author_id"],
                    "name": r["author_name"] or "", "avatar": r["avatar"] or "",
                    "bio": r["signature"] or "", "followers": r["follower_count"] or 0,
                    "verified": bool(r["verified"]), "home": r["home_url"] or "",
                    "last_sync": r["last_synced_at"],
                },
            )
        else:
            bind.execute(
                sa.text(
                    """
                    UPDATE creator_profile
                       SET author_name   = COALESCE(NULLIF(author_name, ''), :name),
                           avatar        = COALESCE(NULLIF(avatar, ''), :avatar),
                           bio           = COALESCE(NULLIF(bio, ''), :bio),
                           homepage_url  = COALESCE(NULLIF(homepage_url, ''), :home),
                           follower_count= GREATEST(follower_count, :followers),
                           verified      = (verified OR :verified),
                           updated_at    = now()
                     WHERE id = :pid
                    """
                ),
                {
                    "name": r["author_name"] or "", "avatar": r["avatar"] or "",
                    "bio": r["signature"] or "", "home": r["home_url"] or "",
                    "followers": r["follower_count"] or 0,
                    "verified": bool(r["verified"]), "pid": prof[0],
                },
            )

    # ---- 2) 清掉历史重复行（唯一约束要求先无重复）----
    bind.execute(
        sa.text(
            """
            DELETE FROM subscription a USING subscription b
             WHERE a.id > b.id
               AND a.account_id = b.account_id
               AND a.source_type = b.source_type
               AND a.remote_id = b.remote_id
            """
        )
    )

    # ---- 3) 加唯一约束 + 删旧表 ----
    op.create_unique_constraint(
        "uq_subscription_account_source_remote",
        "subscription",
        ["account_id", "source_type", "remote_id"],
    )
    op.drop_index("ix_followed_creator_platform_account", table_name="followed_creator")
    op.drop_table("followed_creator")


def downgrade() -> None:
    op.create_table(
        "followed_creator",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("author_id", sa.String(length=128), nullable=False),
        sa.Column("author_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("avatar", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("home_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("follower_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("like_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("full_sync", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_followed_creator_platform_account", "followed_creator", ["platform", "account_id"])
    op.drop_constraint("uq_subscription_account_source_remote", "subscription", type_="unique")

    # 把 creator 订阅还原成关注博主行
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO followed_creator
                (account_id, platform, author_id, author_name, avatar, home_url,
                 signature, verified, follower_count, like_count,
                 sync_enabled, full_sync, last_synced_at, raw, created_at, updated_at)
            SELECT s.account_id, s.platform, s.remote_id, s.title,
                   COALESCE(p.avatar, ''), COALESCE(p.homepage_url, ''),
                   COALESCE(p.bio, ''), COALESCE(p.verified, false),
                   COALESCE(p.follower_count, 0), 0,
                   s.enabled, NOT s.only_new, s.last_sync_at, '{}'::json, now(), now()
              FROM subscription s
              LEFT JOIN creator_profile p
                ON p.platform = s.platform AND p.author_id = s.remote_id
             WHERE s.source_type = 'creator'
            """
        )
    )
