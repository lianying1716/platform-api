"""tiered processing

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05 20:11:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 新增列（NOT NULL，带 server_default 兼容既有行）
    op.add_column('content_item', sa.Column('duration_sec', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('content_item', sa.Column('auto_category', sa.String(length=32), nullable=False, server_default=''))
    op.add_column('content_item', sa.Column('category_confidence', sa.Float(), nullable=False, server_default='0'))
    op.add_column('media_asset', sa.Column('process_tier', sa.String(length=8), nullable=False, server_default=''))
    op.add_column('media_asset', sa.Column('frames_status', sa.String(length=16), nullable=False, server_default='pending'))
    op.add_column('media_asset', sa.Column('understand_status', sa.String(length=16), nullable=False, server_default='pending'))

    op.create_table('asset_frame',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('scene_index', sa.Integer(), nullable=False),
    sa.Column('timestamp_sec', sa.Float(), nullable=False),
    sa.Column('path', sa.String(length=512), nullable=False),
    sa.Column('is_keyframe', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['media_asset.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('asset_id', 'scene_index', name='uq_asset_frame_asset_scene')
    )
    op.create_index(op.f('ix_asset_frame_asset_id'), 'asset_frame', ['asset_id'], unique=False)

    op.create_table('video_understanding',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('category_confidence', sa.Float(), nullable=False),
    sa.Column('category_reason', sa.String(length=512), nullable=False),
    sa.Column('dimensions', sa.JSON(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('timeline', sa.JSON(), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('usage', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.String(length=512), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['media_asset.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_video_understanding_asset_id'), 'video_understanding', ['asset_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_video_understanding_asset_id'), table_name='video_understanding')
    op.drop_table('video_understanding')
    op.drop_index(op.f('ix_asset_frame_asset_id'), table_name='asset_frame')
    op.drop_table('asset_frame')
    op.drop_column('media_asset', 'understand_status')
    op.drop_column('media_asset', 'frames_status')
    op.drop_column('media_asset', 'process_tier')
    op.drop_column('content_item', 'category_confidence')
    op.drop_column('content_item', 'auto_category')
    op.drop_column('content_item', 'duration_sec')
