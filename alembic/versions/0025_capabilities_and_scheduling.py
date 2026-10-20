"""capabilities, three-state config, comment modes, and crawl scheduling

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-18 23:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Existing rows were created with non-null defaults.  The old application
    # could not distinguish an untouched 360/False from an explicit choice, so
    # the only safe migration is to preserve the observed behaviour as explicit
    # values.  New rows get NULL and inherit globals.
    with op.batch_alter_table("subscription") as batch:
        batch.alter_column(
            "interval_minutes",
            existing_type=sa.Integer(),
            nullable=True,
            existing_server_default=None,
        )
        batch.alter_column(
            "auto_transcribe",
            existing_type=sa.Boolean(),
            nullable=True,
            existing_server_default=None,
        )
        batch.alter_column(
            "auto_analyze",
            existing_type=sa.Boolean(),
            nullable=True,
            existing_server_default=None,
        )
        batch.add_column(
            sa.Column("comment_mode", sa.String(length=16), nullable=False, server_default="gate")
        )

    with op.batch_alter_table("crawl_task") as batch:
        batch.add_column(
            sa.Column("comment_mode", sa.String(length=16), nullable=False, server_default="off")
        )
        batch.add_column(sa.Column("last_attempt_at", sa.DateTime(), nullable=True))

    if bind.dialect.name != "sqlite":
        op.execute(
            "UPDATE subscription SET comment_mode = CASE WHEN with_comments THEN 'force' ELSE 'gate' END"
        )
        op.execute(
            "UPDATE crawl_task SET comment_mode = CASE WHEN with_comments THEN 'force' ELSE 'off' END"
        )
        op.drop_column("subscription", "with_comments")
        op.drop_column("crawl_task", "with_comments")
    else:
        # SQLite batch mode is required for dropping columns on older SQLite.
        with op.batch_alter_table("subscription") as batch:
            batch.drop_column("with_comments")
        with op.batch_alter_table("crawl_task") as batch:
            batch.drop_column("with_comments")


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("subscription") as batch:
        batch.add_column(
            sa.Column("with_comments", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
    with op.batch_alter_table("crawl_task") as batch:
        batch.add_column(
            sa.Column("with_comments", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch.drop_column("last_attempt_at")

    if bind.dialect.name != "sqlite":
        op.execute("UPDATE subscription SET with_comments = (comment_mode = 'force')")
        op.execute("UPDATE crawl_task SET with_comments = (comment_mode = 'force')")
        op.drop_column("subscription", "comment_mode")
        op.drop_column("crawl_task", "comment_mode")
    else:
        with op.batch_alter_table("subscription") as batch:
            batch.drop_column("comment_mode")
        with op.batch_alter_table("crawl_task") as batch:
            batch.drop_column("comment_mode")

    with op.batch_alter_table("subscription") as batch:
        batch.alter_column(
            "auto_analyze",
            existing_type=sa.Boolean(),
            nullable=False,
            existing_server_default=sa.text("false"),
        )
        batch.alter_column(
            "auto_transcribe",
            existing_type=sa.Boolean(),
            nullable=False,
            existing_server_default=sa.text("false"),
        )
        batch.alter_column(
            "interval_minutes",
            existing_type=sa.Integer(),
            nullable=False,
            existing_server_default=sa.text("360"),
        )
