"""content-processing workflow runs and dependency-aware steps

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-19 00:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("media_asset.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="content_processing"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="waiting"),
        sa.Column("error", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_pipeline_run_asset_id", "pipeline_run", ["asset_id"])
    op.create_index("ix_pipeline_run_kind", "pipeline_run", ["kind"])
    op.create_index("ix_pipeline_run_status", "pipeline_run", ["status"])

    op.create_table(
        "pipeline_step",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("pipeline_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="waiting_prerequisite",
        ),
        sa.Column("depends_on", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("job_queue.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("run_id", "step", name="uq_pipeline_step_run_step"),
    )
    op.create_index("ix_pipeline_step_run_id", "pipeline_step", ["run_id"])
    op.create_index("ix_pipeline_step_step", "pipeline_step", ["step"])
    op.create_index("ix_pipeline_step_status", "pipeline_step", ["status"])
    op.create_index("ix_pipeline_step_job_id", "pipeline_step", ["job_id"])
    op.create_index("ix_pipeline_step_status_job", "pipeline_step", ["status", "job_id"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_step_status_job", table_name="pipeline_step")
    op.drop_index("ix_pipeline_step_job_id", table_name="pipeline_step")
    op.drop_index("ix_pipeline_step_status", table_name="pipeline_step")
    op.drop_index("ix_pipeline_step_step", table_name="pipeline_step")
    op.drop_index("ix_pipeline_step_run_id", table_name="pipeline_step")
    op.drop_table("pipeline_step")
    op.drop_index("ix_pipeline_run_status", table_name="pipeline_run")
    op.drop_index("ix_pipeline_run_kind", table_name="pipeline_run")
    op.drop_index("ix_pipeline_run_asset_id", table_name="pipeline_run")
    op.drop_table("pipeline_run")
