"""Represent unknown engagement metrics as NULL

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-20 23:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in ("play", "like", "comment", "favorite", "share"):
        op.alter_column(
            "content_metric_snapshot",
            column,
            existing_type=sa.BigInteger(),
            nullable=True,
            existing_nullable=False,
        )


def downgrade() -> None:
    for column in ("play", "like", "comment", "favorite", "share"):
        # ``like`` is a PostgreSQL keyword; quote every identifier rather than
        # relying on the current database's lowercase parser.
        quoted = f'"{column}"'
        op.execute(f"UPDATE content_metric_snapshot SET {quoted} = 0 WHERE {quoted} IS NULL")
        op.alter_column(
            "content_metric_snapshot",
            column,
            existing_type=sa.BigInteger(),
            nullable=False,
            existing_nullable=True,
        )
