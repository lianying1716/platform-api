"""Transactional storage lifecycle: root validation, guarded deletion and asset cleanup."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.queue import NonRetryableJobError, enqueue
from app.models import (
    AnalysisResult,
    ContentItem,
    DeletionAudit,
    JobQueue,
    MediaAsset,
    PlatformAccount,
    StorageRoot,
    Subscription,
    Transcript,
    VideoUnderstanding,
    ViralBreakdown,
)

logger = logging.getLogger("platform.storage")


class StorageRootInUse(ValueError):
    """A storage root still has references and cannot be deleted."""


def get_live_asset(db: Session, asset_id: int) -> MediaAsset | None:
    """Return an asset only when it is still visible (not soft-deleted).

    Centralising this predicate prevents new asset-level endpoints from
    accidentally serving rows that are waiting for asynchronous cleanup.
    """
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.deleted_at is not None:
        return None
    return asset


def _root_references(db: Session, root_id: int) -> dict[str, int]:
    """Count every entity whose effective storage root is ``root_id``.

    A NULL binding is not an absence of use: accounts, subscriptions and legacy
    assets with NULL storage_root_id resolve to the effective default root.  The
    deletion gate must use the same fallback as ``core.storage`` or it can report
    zero references for a root that is still the active destination for new
    downloads and the owner of old assets.
    """
    root = db.get(StorageRoot, root_id)
    if root is None:
        raise KeyError(root_id)
    default_root = storage.get_default_root(db)
    is_effective_default = default_root is not None and default_root.id == root_id

    account_stmt = select(func.count(PlatformAccount.id)).where(
        PlatformAccount.storage_root_id == root_id
    )
    subscription_stmt = select(func.count(Subscription.id)).where(
        Subscription.storage_root_id == root_id
    )
    asset_stmt = select(func.count(MediaAsset.id)).where(MediaAsset.storage_root_id == root_id)

    if is_effective_default:
        account_stmt = select(func.count(PlatformAccount.id)).where(
            (PlatformAccount.storage_root_id == root_id)
            | (PlatformAccount.storage_root_id.is_(None))
        )
        # A subscription with an explicit root uses that root; one inheriting
        # its account uses the account's effective root.
        account_ids_with_default = select(PlatformAccount.id).where(
            (PlatformAccount.storage_root_id == root_id)
            | (PlatformAccount.storage_root_id.is_(None))
        )
        subscription_stmt = select(func.count(Subscription.id)).where(
            (Subscription.storage_root_id == root_id)
            | (
                Subscription.storage_root_id.is_(None)
                & Subscription.account_id.in_(account_ids_with_default)
            )
        )
        # Legacy assets with NULL bindings resolve directly to the default root,
        # regardless of their content's account.
        asset_stmt = select(func.count(MediaAsset.id)).where(
            (MediaAsset.storage_root_id == root_id) | (MediaAsset.storage_root_id.is_(None))
        )

    return {
        "accounts": int(db.scalar(account_stmt) or 0),
        "subscriptions": int(db.scalar(subscription_stmt) or 0),
        "assets": int(db.scalar(asset_stmt) or 0),
    }


def delete_root(db: Session, root_id: int) -> dict[str, int]:
    """Delete an unreferenced root; return counts even when deletion is refused."""
    root = db.get(StorageRoot, root_id)
    if root is None:
        raise KeyError(root_id)
    refs = _root_references(db, root_id)
    if any(refs.values()):
        raise StorageRootInUse(str(refs))
    db.delete(root)
    db.commit()
    return refs


def request_asset_deletion(
    db: Session,
    asset_id: int,
    *,
    actor: str,
    reason: str = "",
) -> tuple[MediaAsset, DeletionAudit, JobQueue]:
    """Hide an asset immediately and enqueue file cleanup.

    The row is kept until the worker has removed files so a failed cleanup can be
    retried and audited; lists/details already filter ``deleted_at``.
    """
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise LookupError(asset_id)
    content = db.get(ContentItem, asset.content_id)
    audit = DeletionAudit(
        asset_id=asset.id,
        content_id=asset.content_id,
        account_id=content.account_id if content else None,
        actor=actor[:64],
        reason=(reason or "")[:500],
        status="pending",
    )
    db.add(audit)
    asset.deleted_at = datetime.utcnow()
    # Any already queued work for this asset is now obsolete.  Running work
    # cannot be interrupted safely, but handlers re-check ``deleted_at`` at their
    # next durable checkpoint; queued work is removed so the worker does not
    # start expensive or destructive work after the user deleted the asset.
    for stale in db.scalars(
        select(JobQueue).where(
            JobQueue.job_type != "delete_asset",
            JobQueue.status == "queued",
        )
    ).all():
        try:
            stale_asset_id = int((stale.payload or {}).get("asset_id"))
        except (TypeError, ValueError):
            continue
        if stale_asset_id == asset.id:
            db.delete(stale)
    db.flush()
    job = enqueue(
        db,
        "delete_asset",
        {"asset_id": asset.id, "audit_id": audit.id},
        dedup=True,
        commit=False,
    )
    db.commit()
    db.refresh(asset)
    db.refresh(audit)
    db.refresh(job)
    return asset, audit, job


def _remove_asset_files(db: Session, asset: MediaAsset) -> None:
    """Remove the asset directory/files; tolerate already-missing paths."""
    try:
        root = storage.resolve_asset_root(db, asset)
    except storage.StorageUnavailable:
        # A deleted root means there is nowhere left to clean; the DB row can go.
        return
    # Unlike a missing root, an unsafe path is not "nothing to clean": fail the
    # job so the audit stays failed and an operator can inspect the data.  The
    # boundary is the owning storage root, never the filesystem root.
    directory = storage.resolve_asset_dir(db, asset)
    target = Path(directory)
    if not target.exists():
        return
    # Only remove the asset's own directory, never the storage root itself.
    resolved_root = Path(root.path).resolve()
    if storage.normalize_root_key(str(target)) == storage.normalize_root_key(str(resolved_root)):
        # Legacy assets may point at the root itself; delete known files only.
        for name in (asset.video_file, asset.audio_file):
            if name:
                safe = storage.resolve_asset_file_path(str(target), name)
                if safe:
                    Path(safe).unlink(missing_ok=True)
        return
    shutil.rmtree(target, ignore_errors=False)


def _remove_cover_cache(asset_id: int) -> None:
    """Remove the local cover-cache pair for a deleted asset.

    Cover cache lives outside ``storage_root`` (under ``data_dir``), so it would
    otherwise outlive the DB row.  Failure is logged but does not block deletion:
    a stale cache entry is harmless and can be garbage-collected separately.
    """
    try:
        from app.config import settings

        cache_dir = Path(settings.data_dir) / "cover_cache"
        (cache_dir / f"{asset_id}.json").unlink(missing_ok=True)
        (cache_dir / f"{asset_id}.bin").unlink(missing_ok=True)
    except OSError:
        logger.warning("素材 #%s 封面缓存清理失败，已忽略", asset_id, exc_info=True)


def _delete_asset_children(db: Session, asset_id: int) -> None:
    """Delete asset-owned rows explicitly.

    PostgreSQL has ``ON DELETE CASCADE``, but local SQLite and older installs may
    not.  Explicit deletes keep the lifecycle contract identical across dialects.
    Audit rows (``model_call_attempt`` / ``deletion_audit``) are intentionally
    retained for investigation.
    """
    for model in (Transcript, AnalysisResult, VideoUnderstanding, ViralBreakdown):
        db.execute(delete(model).where(model.asset_id == asset_id))


def complete_asset_deletion(db: Session, payload: dict) -> None:
    """Worker implementation for ``delete_asset``: audit transition + physical cleanup."""
    try:
        asset_id = int(payload["asset_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NonRetryableJobError("delete_asset 缺少有效 asset_id") from exc
    asset = db.get(MediaAsset, asset_id)
    audit = db.get(DeletionAudit, payload.get("audit_id")) if payload.get("audit_id") else None
    if asset is None:
        _remove_cover_cache(asset_id)
        if audit is not None and audit.status != "completed":
            audit.status = "completed"
            audit.finished_at = datetime.utcnow()
            db.commit()
        return
    try:
        _remove_asset_files(db, asset)
    except Exception as exc:  # noqa: BLE001 file cleanup must be retryable
        if audit is not None:
            audit.status, audit.error = "failed", str(exc)[:500]
            db.commit()
        raise
    _remove_cover_cache(asset_id)
    _delete_asset_children(db, asset_id)
    db.delete(asset)
    if audit is not None:
        audit.status = "completed"
        audit.error = ""
        audit.finished_at = datetime.utcnow()
    db.commit()
    logger.info("素材 #%s 文件清理完成", asset_id)


def recover_pending_deletions(db: Session) -> int:
    """Re-enqueue pending deletion work missed by a crash between soft-delete and worker claim.

    Recovery must be per-asset.  A single live deletion for asset A must not
    suppress recovery for asset B; that turns an interrupted B deletion into a
    permanent row/file mismatch.
    """
    covered: set[int] = set()
    for job in db.scalars(
        select(JobQueue).where(
            JobQueue.job_type == "delete_asset",
            JobQueue.status.in_(("queued", "running")),
        )
    ).all():
        try:
            asset_id = int((job.payload or {}).get("asset_id"))
        except (TypeError, ValueError):
            continue
        if job.status == "queued":
            covered.add(asset_id)
            continue
        # A running job with a fresh heartbeat is genuinely alive.  A stale one
        # is reclaimed by ``queue.reset_stale_running``; leave it alone here so
        # the janitor can reset it without creating a duplicate.
        heartbeat = job.heartbeat_at or job.locked_at or job.updated_at
        if heartbeat is not None and heartbeat >= datetime.utcnow() - timedelta(minutes=30):
            covered.add(asset_id)
    count = 0
    for asset in db.scalars(select(MediaAsset).where(MediaAsset.deleted_at.is_not(None))).all():
        if asset.id in covered:
            continue
        audit = db.scalar(
            select(DeletionAudit)
            .where(DeletionAudit.asset_id == asset.id, DeletionAudit.status == "pending")
            .order_by(DeletionAudit.id.desc())
            .limit(1)
        )
        if audit is None:
            audit = DeletionAudit(
                asset_id=asset.id,
                content_id=asset.content_id,
                actor="janitor",
                reason="恢复中断的删除任务",
                status="pending",
            )
            db.add(audit)
            db.flush()
        enqueue(
            db,
            "delete_asset",
            {"asset_id": asset.id, "audit_id": audit.id},
            dedup=True,
            commit=False,
        )
        count += 1
    if count:
        db.commit()
    return count


def scan_orphan_files(db: Session, *, root_id: int | None = None) -> dict[str, int | list[str]]:
    """Report files under storage roots that are not referenced by a live asset.

    This is intentionally a report-only scan: deleting an unknown file is far more
    dangerous than leaving it for an operator to inspect.  Paths in the response are
    relative, so the endpoint can safely show them in the admin UI.
    """
    roots = [db.get(StorageRoot, root_id)] if root_id is not None else list(db.scalars(select(StorageRoot)))
    roots = [r for r in roots if r is not None]
    known: set[tuple[str, str]] = set()
    for asset in db.scalars(select(MediaAsset).where(MediaAsset.deleted_at.is_(None))).all():
        try:
            directory = storage.resolve_asset_dir(db, asset)
        except storage.StorageUnavailable:
            continue
        key = storage.normalize_root_key(directory)
        for name in (asset.video_file, asset.audio_file):
            if name:
                known.add((key, name))
    report: list[str] = []
    for root in roots:
        base = Path(root.path)
        if not base.is_dir():
            continue
        for file in base.rglob("*"):
            if not file.is_file():
                continue
            rel = file.relative_to(base).as_posix()
            directory_key = storage.normalize_root_key(str(file.parent))
            if (directory_key, file.name) not in known:
                report.append(rel)
    return {"orphan_count": len(report), "orphans": sorted(report)[:500]}
