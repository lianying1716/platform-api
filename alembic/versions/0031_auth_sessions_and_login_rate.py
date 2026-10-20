"""Persisted admin sessions and login-attempt budget

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-19 23:59:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0031"
down_revision: Union[str, Sequence[str], None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_session",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admin_session_expires_at", "admin_session", ["expires_at"])

    op.create_table(
        "auth_rate_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_auth_rate_event_key", "auth_rate_event", ["key"])
    op.create_index("ix_auth_rate_event_created_at", "auth_rate_event", ["created_at"])
    op.create_index("ix_auth_rate_event_key_created", "auth_rate_event", ["key", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_rate_event_key_created", table_name="auth_rate_event")
    op.drop_index("ix_auth_rate_event_created_at", table_name="auth_rate_event")
    op.drop_index("ix_auth_rate_event_key", table_name="auth_rate_event")
    op.drop_table("auth_rate_event")
    op.drop_index("ix_admin_session_expires_at", table_name="admin_session")
    op.drop_table("admin_session")
