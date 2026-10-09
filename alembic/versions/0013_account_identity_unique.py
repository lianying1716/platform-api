"""阶段 1：同平台非空 external_id 唯一，防并发重复账号。

历史数据可能已有同 (platform, external_id) 的重复账号（旧逻辑每次扫码新建）。
迁移先合并重复账号：保留最小 id，把 subscription/crawl_task/crawl_run/content_item
上的引用改到保留账号，再删除重复行；随后建部分唯一索引。

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-09 07:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _merge_duplicate_accounts(bind) -> None:
    duplicates = bind.execute(
        sa.text(
            """
            SELECT platform, external_id, MIN(id) AS keep_id
              FROM platform_account
             WHERE is_system = false AND external_id <> ''
             GROUP BY platform, external_id
            HAVING COUNT(*) > 1
            """
        )
    ).mappings().all()
    for row in duplicates:
        keep_id = int(row["keep_id"])
        dup_ids = [
            int(r[0])
            for r in bind.execute(
                sa.text(
                    """
                    SELECT id FROM platform_account
                     WHERE platform = :p AND external_id = :e AND is_system = false AND id <> :keep
                    """
                ),
                {"p": row["platform"], "e": row["external_id"], "keep": keep_id},
            ).all()
        ]
        if not dup_ids:
            continue
        # 迁移内容/订阅/任务引用；凭证与审计记录随重复账号删除。
        for table, column in (
            ("subscription", "account_id"),
            ("crawl_task", "account_id"),
            ("crawl_run", "account_id"),
            ("content_item", "account_id"),
        ):
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :keep WHERE {column} IN :dups").bindparams(
                    sa.bindparam("dups", expanding=True)
                ),
                {"keep": keep_id, "dups": dup_ids},
            )
        bind.execute(
            sa.text("DELETE FROM platform_account WHERE id IN :dups").bindparams(
                sa.bindparam("dups", expanding=True)
            ),
            {"dups": dup_ids},
        )


def upgrade() -> None:
    bind = op.get_bind()
    _merge_duplicate_accounts(bind)
    op.create_index(
        "uq_platform_account_platform_external_id",
        "platform_account",
        ["platform", "external_id"],
        unique=True,
        postgresql_where=sa.text("is_system = false AND external_id <> ''"),
        sqlite_where=sa.text("is_system = 0 AND external_id <> ''"),
    )


def downgrade() -> None:
    op.drop_index("uq_platform_account_platform_external_id", table_name="platform_account")
