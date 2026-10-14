"""platform_account.sync_enabled：账号级「同步总开关」（沉默期）。

需求：账号管理页每个账号一个总开关。打开 = 该账号按配置正常同步；关闭 = 该账号进入
「沉默期」——所有同步/调用一律禁止（源头拦截，不靠每个调用点各自记得判）。默认 True，
保证存量账号升级后行为不变。

与订阅级 enabled 的关系：总开关是账号维度的**与门**，订阅/博主的各自开关照旧保留；
总开关关掉后，即便订阅还开着也不排队、不执行。

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-13 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "platform_account",
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # 存量账号默认开启，与 server_default 一致（显式回填，避免老行 NULL 语义歧义）。
    op.execute("UPDATE platform_account SET sync_enabled = true")


def downgrade() -> None:
    op.drop_column("platform_account", "sync_enabled")
