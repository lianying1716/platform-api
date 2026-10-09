"""阶段 3：内容来源表 + 删除审计表。

账号/订阅删除不再物理删除素材；content_source 记录账号内多订阅来源，
取消订阅只 active=false。素材删除必须走显式入口并写 deletion_audit。

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-09 08:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscription", sa.Column("deleted_at", sa.DateTime(), nullable=True))

    op.create_table(
        "content_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_id", sa.Integer(), sa.ForeignKey("content_item.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("platform_account.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("subscription.id", ondelete="SET NULL"), nullable=True),
        sa.Column("link_kind", sa.String(length=16), nullable=False, server_default="subscription"),
        sa.Column("source_ref", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_content_source_content_id", "content_source", ["content_id"])
    op.create_index("ix_content_source_account_id", "content_source", ["account_id"])
    op.create_index("ix_content_source_subscription_id", "content_source", ["subscription_id"])
    op.create_index("ix_content_source_active", "content_source", ["active"])
    op.create_index(
        "uq_content_source_content_subscription",
        "content_source",
        ["content_id", "subscription_id"],
        unique=True,
        postgresql_where=sa.text("subscription_id IS NOT NULL"),
    )

    op.create_table(
        "deletion_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="admin"),
        sa.Column("reason", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_deletion_audit_asset_id", "deletion_audit", ["asset_id"])
    op.create_index("ix_deletion_audit_content_id", "deletion_audit", ["content_id"])
    op.create_index("ix_deletion_audit_account_id", "deletion_audit", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_deletion_audit_account_id", table_name="deletion_audit")
    op.drop_index("ix_deletion_audit_content_id", table_name="deletion_audit")
    op.drop_index("ix_deletion_audit_asset_id", table_name="deletion_audit")
    op.drop_table("deletion_audit")

    op.drop_index("uq_content_source_content_subscription", table_name="content_source")
    op.drop_index("ix_content_source_active", table_name="content_source")
    op.drop_index("ix_content_source_subscription_id", table_name="content_source")
    op.drop_index("ix_content_source_account_id", table_name="content_source")
    op.drop_index("ix_content_source_content_id", table_name="content_source")
    op.drop_table("content_source")
    op.drop_column("subscription", "deleted_at")
