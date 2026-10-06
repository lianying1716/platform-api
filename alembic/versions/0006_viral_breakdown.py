"""viral breakdown report

新增 viral_breakdown 表：一条素材一份「爆款拆解报告」，asset_id 唯一。

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema：新增拆解报告表。"""
    op.create_table(
        "viral_breakdown",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("comment_insights", sa.JSON(), nullable=False),
        sa.Column("data_context", sa.JSON(), nullable=False),
        sa.Column("understanding_available", sa.Boolean(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["media_asset.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_viral_breakdown_asset_id"), "viral_breakdown", ["asset_id"], unique=True
    )


def downgrade() -> None:
    """Downgrade schema：删除拆解报告表。"""
    op.drop_index(op.f("ix_viral_breakdown_asset_id"), table_name="viral_breakdown")
    op.drop_table("viral_breakdown")
