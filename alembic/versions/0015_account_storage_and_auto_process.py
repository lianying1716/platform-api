"""阶段：账号级存储根 + 订阅自动加工开关。

- platform_account 增加 storage_root_id（NULL = 用默认根）：账户可各自指定根目录，
  也可共用同一个根。
- subscription 增加 auto_transcribe / auto_analyze（订阅级显式 opt-in）。
- subscription.subdir_template 默认值加入 {account} 段，让账户目录成为布局的一环。

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-09 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "platform_account",
        sa.Column("storage_root_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_platform_account_storage_root",
        "platform_account",
        "storage_root",
        ["storage_root_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "subscription",
        sa.Column("storage_root_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_subscription_storage_root",
        "subscription",
        "storage_root",
        ["storage_root_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "subscription",
        sa.Column("auto_transcribe", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "subscription",
        sa.Column("auto_analyze", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("subscription", "auto_analyze")
    op.drop_column("subscription", "auto_transcribe")
    op.drop_constraint("fk_subscription_storage_root", "subscription", type_="foreignkey")
    op.drop_column("subscription", "storage_root_id")
    op.drop_constraint("fk_platform_account_storage_root", "platform_account", type_="foreignkey")
    op.drop_column("platform_account", "storage_root_id")
