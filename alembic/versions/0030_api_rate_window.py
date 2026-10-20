"""durable API key rate-limit window

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-19 23:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030"
down_revision: Union[str, Sequence[str], None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_rate_window",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key_id", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["key_id"], ["api_key.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("key_id", "window_start", name="uq_api_rate_window_key_start"),
    )
    op.create_index("ix_api_rate_window_key_id", "api_rate_window", ["key_id"])
    op.create_index("ix_api_rate_window_window_start", "api_rate_window", ["window_start"])


def downgrade() -> None:
    op.drop_index("ix_api_rate_window_window_start", table_name="api_rate_window")
    op.drop_index("ix_api_rate_window_key_id", table_name="api_rate_window")
    op.drop_table("api_rate_window")
