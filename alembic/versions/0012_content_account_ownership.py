"""阶段 0：content_item 加账号所有权，唯一性改为 (platform, external_id, account_id)

背景：已拍板“同一视频跨账号 = 独立素材”。历史 content_item 没有 account_id，
只能按 source_kind 分类回填：
- source_kind='subscription' 且 source_id 命中 subscription.id：可靠映射；
- 其余（crawl/import/孤儿/无法映射）：挂到每平台一个隐藏系统账号，绝不猜真实账号。

同时给 platform_account 增加 is_system / last_crawl_used_at，给 crawl_run 增加
account_id，为阶段 4 的采集轮换预留；本迁移即可安全使用。

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-09 06:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _system_account_id(bind, platform: str) -> int:
    """保证每个平台有一个隐藏系统账号，返回其 id。"""
    existing = bind.execute(
        sa.text(
            "SELECT id FROM platform_account WHERE platform = :p AND is_system = true LIMIT 1"
        ),
        {"p": platform},
    ).scalar()
    if existing is not None:
        return int(existing)
    # 历史库可能手动指定过 id；先同步序列，避免系统账号撞主键。
    bind.execute(
        sa.text(
            "SELECT setval(pg_get_serial_sequence('platform_account','id'), "
            "COALESCE((SELECT MAX(id) FROM platform_account), 1), true)"
        )
    )
    return int(
        bind.execute(
            sa.text(
                """
                INSERT INTO platform_account
                    (platform, display_name, external_id, credential_kind, status,
                     status_message, is_system, created_at, updated_at)
                VALUES
                    (:p, '系统归属（历史迁移）', '__system_owner__', 'system', 'active',
                     '', true, now(), now())
                RETURNING id
                """
            ),
            {"p": platform},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 账号表：系统标记 + 采集最近使用时间
    op.add_column(
        "platform_account",
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("platform_account", sa.Column("last_crawl_used_at", sa.DateTime(), nullable=True))

    # 2) 采集运行记录：本次实际使用的账号
    op.add_column("crawl_task", sa.Column("account_id", sa.Integer(), nullable=True))
    op.create_index("ix_crawl_task_account_id", "crawl_task", ["account_id"])
    op.create_foreign_key(
        "fk_crawl_task_account_id", "crawl_task", "platform_account", ["account_id"], ["id"],
        ondelete="SET NULL",
    )
    op.add_column("crawl_run", sa.Column("account_id", sa.Integer(), nullable=True))
    op.create_index("ix_crawl_run_account_id", "crawl_run", ["account_id"])
    op.create_foreign_key(
        "fk_crawl_run_account_id", "crawl_run", "platform_account", ["account_id"], ["id"],
        ondelete="SET NULL",
    )

    # 3) 内容表先加可空账号，按来源分类回填；绝不把 crawl_task.id 当 subscription.id
    op.add_column("content_item", sa.Column("account_id", sa.Integer(), nullable=True))

    bind.execute(
        sa.text(
            """
            UPDATE content_item AS ci
               SET account_id = s.account_id
              FROM subscription AS s
             WHERE ci.source_kind = 'subscription'
               AND ci.source_id IS NOT NULL
               AND ci.source_id = s.id
            """
        )
    )

    # 拿不到可靠归属的行，逐平台挂隐藏系统账号（含 crawl/import/孤儿）
    orphan_platforms = [
        row[0]
        for row in bind.execute(
            sa.text("SELECT DISTINCT platform FROM content_item WHERE account_id IS NULL")
        ).all()
    ]
    for platform in orphan_platforms:
        system_id = _system_account_id(bind, platform)
        bind.execute(
            sa.text(
                "UPDATE content_item SET account_id = :aid WHERE platform = :p AND account_id IS NULL"
            ),
            {"aid": system_id, "p": platform},
        )

    # 4) 收紧非空 + 外键 + 账号级唯一约束
    op.alter_column("content_item", "account_id", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key(
        "fk_content_item_account_id", "content_item", "platform_account", ["account_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_content_item_account_id", "content_item", ["account_id"])
    op.create_unique_constraint(
        "uq_content_item_platform_external_account",
        "content_item",
        ["platform", "external_id", "account_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_content_item_platform_external_account", "content_item", type_="unique")
    op.drop_index("ix_content_item_account_id", table_name="content_item")
    op.drop_constraint("fk_content_item_account_id", "content_item", type_="foreignkey")
    op.drop_column("content_item", "account_id")

    op.drop_constraint("fk_crawl_run_account_id", "crawl_run", type_="foreignkey")
    op.drop_index("ix_crawl_run_account_id", table_name="crawl_run")
    op.drop_column("crawl_run", "account_id")

    op.drop_constraint("fk_crawl_task_account_id", "crawl_task", type_="foreignkey")
    op.drop_index("ix_crawl_task_account_id", table_name="crawl_task")
    op.drop_column("crawl_task", "account_id")

    op.drop_column("platform_account", "last_crawl_used_at")
    op.drop_column("platform_account", "is_system")
