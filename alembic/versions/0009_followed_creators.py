"""followed_creator table (关注博主 + 同步开关)

「关注博主列表」页的卡片数据源：只存某账号关注的人（头像/昵称/主页为真实值），
并按 sync_enabled/full_sync 维护对应订阅。唯一约束 (account_id, author_id)。

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "followed_creator",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("platform_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("author_id", sa.String(length=128), nullable=False),
        sa.Column("author_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("avatar", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("home_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("follower_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("like_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("full_sync", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("account_id", "author_id", name="uq_followed_creator_account_author"),
    )
    op.create_index("ix_followed_creator_account_id", "followed_creator", ["account_id"])
    op.create_index("ix_followed_creator_platform", "followed_creator", ["platform"])
    op.create_index(
        "ix_followed_creator_platform_account", "followed_creator", ["platform", "account_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_followed_creator_platform_account", table_name="followed_creator")
    op.drop_index("ix_followed_creator_platform", table_name="followed_creator")
    op.drop_index("ix_followed_creator_account_id", table_name="followed_creator")
    op.drop_table("followed_creator")
