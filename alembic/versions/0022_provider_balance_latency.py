"""ai_provider 增加余额与延迟缓存列（只做手动刷新）。

需求：供应商卡片上显示「余额」（星算可通过密钥拉取）与「延迟」。
两者都**只手动刷新**——点数字才打一次网，平时展示上次结果，所以需要落库：

- balance_amount / balance_error / balance_updated_at
- latency_ms / latency_error / latency_updated_at

均为可空：NULL = 从未刷新过，前端显示「—」而不是 0（0 会被误读成真实值）。

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_provider", sa.Column("balance_amount", sa.Float(), nullable=True))
    op.add_column(
        "ai_provider",
        sa.Column("balance_error", sa.String(length=200), nullable=False, server_default=""),
    )
    op.add_column("ai_provider", sa.Column("balance_updated_at", sa.DateTime(), nullable=True))
    op.add_column("ai_provider", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column(
        "ai_provider",
        sa.Column("latency_error", sa.String(length=200), nullable=False, server_default=""),
    )
    op.add_column("ai_provider", sa.Column("latency_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_provider", "latency_updated_at")
    op.drop_column("ai_provider", "latency_error")
    op.drop_column("ai_provider", "latency_ms")
    op.drop_column("ai_provider", "balance_updated_at")
    op.drop_column("ai_provider", "balance_error")
    op.drop_column("ai_provider", "balance_amount")
