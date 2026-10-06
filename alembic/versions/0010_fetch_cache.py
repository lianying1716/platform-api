"""fetch_cache table (抓取缓存 + 风控冷却)

通用平台读操作缓存：关注列表 / 博主统计 / 博主投稿；cooldown:* 键记录风控冷却。
按 key 主键，value JSON，expires_at 索引（供清理与查询）。

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fetch_cache",
        sa.Column("key", sa.String(length=256), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_fetch_cache_expires_at", "fetch_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_fetch_cache_expires_at", table_name="fetch_cache")
    op.drop_table("fetch_cache")
