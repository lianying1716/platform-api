"""foundation tables (creator_profile / raw_payload / task_log)

架构地基补全：新增三张地基表
- creator_profile：独立创作者实体，唯一 (platform, author_id)
- raw_payload：原始 JSON 留存，索引 (platform, external_id)
- task_log：结构化任务事件流，索引 (job_type, created_at)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema：新增创作者 / 原始数据 / 任务日志三表。"""
    op.create_table(
        "creator_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("author_id", sa.String(length=128), nullable=False),
        sa.Column("author_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("avatar", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("bio", sa.Text(), nullable=False, server_default=""),
        sa.Column("follower_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("following_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("homepage_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "author_id", name="uq_creator_profile_platform_author"),
    )
    op.create_index(
        op.f("ix_creator_profile_platform"), "creator_profile", ["platform"], unique=False
    )
    op.create_table(
        "raw_payload",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("content_id", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["content_id"], ["content_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_raw_payload_external_id"), "raw_payload", ["external_id"], unique=False
    )
    op.create_index(op.f("ix_raw_payload_platform"), "raw_payload", ["platform"], unique=False)
    op.create_index(
        "ix_raw_payload_platform_external_id",
        "raw_payload",
        ["platform", "external_id"],
        unique=False,
    )
    op.create_table(
        "task_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False, server_default="info"),
        sa.Column("message", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="worker"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["job_id"], ["job_queue.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_log_job_type_created_at",
        "task_log",
        ["job_type", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema：按相反顺序删除三张地基表。"""
    op.drop_index("ix_task_log_job_type_created_at", table_name="task_log")
    op.drop_table("task_log")
    op.drop_index("ix_raw_payload_platform_external_id", table_name="raw_payload")
    op.drop_index(op.f("ix_raw_payload_platform"), table_name="raw_payload")
    op.drop_index(op.f("ix_raw_payload_external_id"), table_name="raw_payload")
    op.drop_table("raw_payload")
    op.drop_index(op.f("ix_creator_profile_platform"), table_name="creator_profile")
    op.drop_table("creator_profile")
