"""execution plane: run traceability, heartbeats and probe timeout state

把 job_queue、sync_run、crawl_run 和 ai_model 的状态串成可对账的执行平面：
- sync_run / crawl_run 记录发起它们的 job_id，运行不再与队列脱钩；
- job / run 增加 heartbeat_at，长任务不会被 locked_at 的固定阈值误回收；
- ai_model 增加 probe_started_at，崩溃遗留的 testing 可被 janitor 收敛。

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-18 21:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_queue", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    op.create_index("ix_job_queue_heartbeat_at", "job_queue", ["heartbeat_at"], unique=False)

    op.add_column("sync_run", sa.Column("job_id", sa.Integer(), nullable=True))
    op.add_column("sync_run", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    op.create_index("ix_sync_run_job_id", "sync_run", ["job_id"], unique=False)
    op.create_index("ix_sync_run_heartbeat_at", "sync_run", ["heartbeat_at"], unique=False)

    op.add_column("crawl_run", sa.Column("job_id", sa.Integer(), nullable=True))
    op.add_column("crawl_run", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    op.create_index("ix_crawl_run_job_id", "crawl_run", ["job_id"], unique=False)
    op.create_index("ix_crawl_run_heartbeat_at", "crawl_run", ["heartbeat_at"], unique=False)

    op.add_column("ai_model", sa.Column("probe_started_at", sa.DateTime(), nullable=True))
    op.create_index("ix_ai_model_probe_started_at", "ai_model", ["probe_started_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_model_probe_started_at", table_name="ai_model")
    op.drop_column("ai_model", "probe_started_at")

    op.drop_index("ix_crawl_run_heartbeat_at", table_name="crawl_run")
    op.drop_index("ix_crawl_run_job_id", table_name="crawl_run")
    op.drop_column("crawl_run", "heartbeat_at")
    op.drop_column("crawl_run", "job_id")

    op.drop_index("ix_sync_run_heartbeat_at", table_name="sync_run")
    op.drop_index("ix_sync_run_job_id", table_name="sync_run")
    op.drop_column("sync_run", "heartbeat_at")
    op.drop_column("sync_run", "job_id")

    op.drop_index("ix_job_queue_heartbeat_at", table_name="job_queue")
    op.drop_column("job_queue", "heartbeat_at")
