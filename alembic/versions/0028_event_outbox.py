"""durable cross-process event outbox for SSE

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-19 22:00:00.000000

The worker process cannot publish into the API process's asyncio queues.  Events
are committed with their business transaction and read by SSE after an id
cursor, which also provides ``Last-Event-ID`` replay.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028"
down_revision: Union[str, Sequence[str], None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_event_outbox_created_at", "event_outbox", ["created_at"])
    # SSE needs an id cursor, and retention needs an age cursor.
    op.create_index("ix_event_outbox_event_id", "event_outbox", ["event", "id"])


def downgrade() -> None:
    op.drop_index("ix_event_outbox_event_id", table_name="event_outbox")
    op.drop_index("ix_event_outbox_created_at", table_name="event_outbox")
    op.drop_table("event_outbox")
