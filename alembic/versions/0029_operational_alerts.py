"""operational alerts with dedupe, acknowledgement and recovery

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-19 23:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("dedup_key", sa.String(length=160), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False, server_default="warning"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("title", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("message", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("job_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["job_id"], ["job_queue.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("dedup_key", name="uq_alert_dedup_key"),
    )
    op.create_index("ix_alert_kind", "alert", ["kind"])
    op.create_index("ix_alert_status", "alert", ["status"])
    op.create_index("ix_alert_job_id", "alert", ["job_id"])
    op.create_index("ix_alert_status_created_at", "alert", ["status", "created_at"])
    op.create_index("ix_alert_job_id_status", "alert", ["job_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_alert_job_id_status", table_name="alert")
    op.drop_index("ix_alert_status_created_at", table_name="alert")
    op.drop_index("ix_alert_job_id", table_name="alert")
    op.drop_index("ix_alert_status", table_name="alert")
    op.drop_index("ix_alert_kind", table_name="alert")
    op.drop_table("alert")
