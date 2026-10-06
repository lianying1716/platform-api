"""understand low_confidence flag

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05 21:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 判类置信度低于阈值时置 true，供前端/人工复核（设计 §4）。
    op.add_column(
        'video_understanding',
        sa.Column('low_confidence', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('video_understanding', 'low_confidence')
