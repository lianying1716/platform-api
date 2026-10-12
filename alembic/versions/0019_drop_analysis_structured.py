"""analysis_result.structured：删除与爆款拆解重复的结构化字段。

背景：`analysis_result.structured` 存的是 `{hook, structure, selling_point}`，
是爆款拆解 `hook` / `structure` 的迷你重复版，且前端从未渲染（全站无引用）。
AI 分析回归纯「摘要 + 标签」的速览定位，深度拆解统一由 viral_breakdown 承担。

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-11 09:50:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite 需要 batch 模式重建表才能落 drop column。
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("analysis_result") as batch:
            batch.drop_column("structured")
    else:
        op.drop_column("analysis_result", "structured")


def downgrade() -> None:
    op.add_column(
        "analysis_result",
        sa.Column("structured", sa.JSON(), nullable=True, server_default=sa.text("'{}'")),
    )
