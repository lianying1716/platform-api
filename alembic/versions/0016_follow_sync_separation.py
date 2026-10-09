"""关注列表 / 收藏 / 点赞 同步分离 + 同步结果追踪。

- subscription.sync_videos：creator 订阅专用。关注关系（enabled）与「是否同步该博主名下视频」
  （sync_videos）是两件事：导入关注列表默认开启关系跟踪，但博主视频必须手动开启。
- sync_run.updated：记录本次同步里被「全量」刷新命中的已有内容条数。
  only_new=false 时旧内容会被覆盖更新，光看 added 看不出效果，必须能观测到 updated。

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-09 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscription",
        sa.Column("sync_videos", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "sync_run",
        sa.Column("updated", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    # 存量关注列表：旧逻辑"导入默认不开启"，与新需求（关注列表默认同步）不符，
    # 统一回填为开启「关系跟踪」。注意这只影响关系跟踪，不拉博主视频
    # （博主视频另由 sync_videos 控制，保持默认关闭）。
    op.execute("UPDATE subscription SET enabled = true WHERE source_type = 'creator'")


def downgrade() -> None:
    op.drop_column("sync_run", "updated")
    op.drop_column("subscription", "sync_videos")
