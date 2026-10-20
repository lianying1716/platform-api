"""bounded queue deduplication and batch retry support

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-19 21:00:00.000000

``job_queue.payload`` is JSON, which PostgreSQL cannot compare for equality.
The old enqueue path therefore loaded every queued/running row and compared
payloads in Python.  This revision stores a canonical SHA-256 key and enforces
uniqueness only for active jobs, so completed/failed rows never block a later
command with the same payload.

Legacy active duplicates are collapsed deterministically before the unique
index is created: the oldest row stays active, later duplicates are marked
error with an explicit reason so an operator can inspect them.
"""

import hashlib
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dedup_key(job_type: str, payload: dict | None) -> str:
    canonical = json.dumps(
        {"job_type": job_type, "payload": payload or {}},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("job_queue", sa.Column("dedup_key", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, job_type, payload FROM job_queue "
            "WHERE status IN ('queued', 'running') ORDER BY id"
        )
    ).mappings()
    kept: dict[str, int] = {}
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        key = _dedup_key(row["job_type"], payload)
        if key in kept:
            bind.execute(
                sa.text(
                    "UPDATE job_queue SET status = 'error', dedup_key = NULL, "
                    "locked_at = NULL, heartbeat_at = NULL, "
                    "error = :error WHERE id = :id"
                ),
                {
                    "id": row["id"],
                    "error": f"迁移 0027 收敛重复活动任务（保留任务 #{kept[key]}）",
                },
            )
            continue
        kept[key] = row["id"]
        bind.execute(
            sa.text("UPDATE job_queue SET dedup_key = :key WHERE id = :id"),
            {"key": key, "id": row["id"]},
        )

    op.create_index(
        "uq_job_queue_active_dedup_key",
        "job_queue",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL AND status IN ('queued', 'running')"),
        sqlite_where=sa.text("dedup_key IS NOT NULL AND status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_job_queue_active_dedup_key", table_name="job_queue")
    op.drop_column("job_queue", "dedup_key")
