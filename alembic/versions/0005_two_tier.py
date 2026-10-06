"""two tier simplification (drop frame extraction)

将素材加工分档从三档（l1 转录 / l2 抽帧 / l3 视频理解）简化为两档
（basic 转录 / advanced 视频理解）：删除 asset_frame 表与 media_asset.frames_status 列，
process_tier 由 String(8) 放宽到 String(16)，并把历史档位值映射到新取值。

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _alter_process_tier(new_type: sa.String) -> None:
    """按方言变迁 process_tier 类型。

    SQLite 不支持直接改列类型（无 ALTER COLUMN），须走 batch 重建表；
    PostgreSQL 直接 ALTER 即可。
    """
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("media_asset") as batch:
            batch.alter_column(
                "process_tier",
                existing_type=sa.String(length=8),
                type_=new_type,
                existing_nullable=False,
                existing_server_default="",
            )
    else:
        op.alter_column(
            "media_asset",
            "process_tier",
            existing_type=sa.String(length=8),
            type_=new_type,
            existing_nullable=False,
        )


def upgrade() -> None:
    """Upgrade schema：删除抽帧产物，档位扩到 basic/advanced。"""
    # 1) 老档位值映射到新取值（先映射，避免删列/表前丢失历史档位信息）。
    op.execute("UPDATE media_asset SET process_tier = 'advanced' WHERE process_tier = 'l3'")
    op.execute("UPDATE media_asset SET process_tier = 'basic' WHERE process_tier IN ('l1', 'l2')")

    # 2) 删除抽帧表与状态列。
    op.drop_index(op.f("ix_asset_frame_asset_id"), table_name="asset_frame")
    op.drop_table("asset_frame")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("media_asset") as batch:
            batch.drop_column("frames_status")
    else:
        op.drop_column("media_asset", "frames_status")

    # 3) 放宽 process_tier 长度（basic/advanced 需要 16）。
    _alter_process_tier(sa.String(length=16))


def downgrade() -> None:
    """Downgrade schema：恢复三档结构与取值。"""
    # 1) process_tier 收回 String(8)。
    _alter_process_tier(sa.String(length=8))

    # 2) 档位值映射回 l1/l2/l3（basic→l1、advanced→l3）。
    op.execute("UPDATE media_asset SET process_tier = 'l3' WHERE process_tier = 'advanced'")
    op.execute("UPDATE media_asset SET process_tier = 'l1' WHERE process_tier = 'basic'")

    # 3) 恢复 frames_status 列。
    op.add_column(
        "media_asset",
        sa.Column("frames_status", sa.String(length=16), nullable=False, server_default="pending"),
    )

    # 4) 恢复 asset_frame 表（与 0003 一致）。
    op.create_table(
        "asset_frame",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("scene_index", sa.Integer(), nullable=False),
        sa.Column("timestamp_sec", sa.Float(), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("is_keyframe", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["media_asset.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "scene_index", name="uq_asset_frame_asset_scene"),
    )
    op.create_index(op.f("ix_asset_frame_asset_id"), "asset_frame", ["asset_id"], unique=False)
