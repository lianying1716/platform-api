"""ai_model.protocol：模型级协议（NULL=继承供应商）+ protocol_source

需求：一个网关常同时暴露多种协议（星算站点根既有 OpenAI 兼容 /v1，也有 Gemini 原生
/v1beta/:generateContent）。供应商级单选无法表达，且不该让用户自己选协议。

本迁移只加两列、不回填：
- protocol 为 NULL = 继承供应商协议，存量行行为与升级前完全一致；
- protocol_source 默认 'inherit'（未判定）。后端实测判定后写 'auto'，
  用户手动指定写 'manual'（manual 不被自动检测覆盖）。

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-14 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_model", sa.Column("protocol", sa.String(length=32), nullable=True))
    op.add_column(
        "ai_model",
        sa.Column(
            "protocol_source",
            sa.String(length=16),
            nullable=False,
            server_default="inherit",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_model", "protocol_source")
    op.drop_column("ai_model", "protocol")
