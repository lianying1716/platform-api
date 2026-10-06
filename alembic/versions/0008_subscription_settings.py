"""subscription settings (min_play / publish_after_days / max_items / only_new / download_quality / with_comments)

订阅同步设置丰富化：subscription 表新增 6 个筛选/截断/画质/评论开关字段，
均为 NOT NULL 且带 server_default，保证既有行升级后有确定取值。

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema：subscription 增加 6 个同步设置列。"""
    op.add_column(
        "subscription",
        sa.Column("min_play", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "subscription",
        sa.Column("publish_after_days", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "subscription",
        sa.Column("max_items", sa.Integer(), nullable=False, server_default="50"),
    )
    op.add_column(
        "subscription",
        sa.Column("only_new", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "subscription",
        sa.Column("download_quality", sa.String(length=16), nullable=False, server_default="best"),
    )
    op.add_column(
        "subscription",
        sa.Column("with_comments", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema：删除 6 个同步设置列（逆序）。"""
    op.drop_column("subscription", "with_comments")
    op.drop_column("subscription", "download_quality")
    op.drop_column("subscription", "only_new")
    op.drop_column("subscription", "max_items")
    op.drop_column("subscription", "publish_after_days")
    op.drop_column("subscription", "min_play")
