"""subscription.last_attempt_at：记录**每次**同步尝试时间，用于失败退避。

为什么需要单独一列而不是复用 last_sync_at：
last_sync_at 只在同步**成功**时更新。调度器按 last_sync_at 判断"是否到期"，
于是持续失败的订阅（账号掉线 / 平台风控）会被每 5 分钟重新入队，形成对平台的
持续打点——这既没用又正好会在风控期加重风险。last_attempt_at 无论成败都更新，
调度器据此退避：失败后要等满一个 interval_minutes 才会再试。

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-09 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscription", sa.Column("last_attempt_at", sa.DateTime(), nullable=True))
    # 存量回填：把已有的成功时间当作最后一次尝试，避免升级后立刻重排所有订阅。
    op.execute("UPDATE subscription SET last_attempt_at = last_sync_at WHERE last_attempt_at IS NULL")


def downgrade() -> None:
    op.drop_column("subscription", "last_attempt_at")
