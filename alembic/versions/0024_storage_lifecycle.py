"""storage lifecycle: soft-delete assets, audit completion and reference protection

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-18 22:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("media_asset", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_media_asset_deleted_at", "media_asset", ["deleted_at"], unique=False)
    op.add_column(
        "deletion_audit",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
    )
    op.add_column(
        "deletion_audit",
        sa.Column("error", sa.String(length=512), nullable=False, server_default=""),
    )
    op.add_column("deletion_audit", sa.Column("finished_at", sa.DateTime(), nullable=True))

    # The application now rejects referenced root deletion with 409. Keep the
    # database as a second line of defence instead of surfacing an FK 500.
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.drop_constraint("media_asset_storage_root_id_fkey", "media_asset", type_="foreignkey")
        op.create_foreign_key(
            "fk_media_asset_storage_root",
            "media_asset",
            "storage_root",
            ["storage_root_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.drop_constraint("fk_media_asset_storage_root", "media_asset", type_="foreignkey")
        op.create_foreign_key(
            "media_asset_storage_root_id_fkey",
            "media_asset",
            "storage_root",
            ["storage_root_id"],
            ["id"],
        )
    op.drop_column("deletion_audit", "finished_at")
    op.drop_column("deletion_audit", "error")
    op.drop_column("deletion_audit", "status")
    op.drop_index("ix_media_asset_deleted_at", table_name="media_asset")
    op.drop_column("media_asset", "deleted_at")
