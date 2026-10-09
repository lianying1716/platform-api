"""定时任务：账号体检 + 浏览器扫码会话回收。"""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.services.account_health import check_account
from app.db import SessionLocal
from app.models import CredentialAuditLog, PlatformAccount

logger = logging.getLogger("platform.scheduler")
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def check_all_accounts() -> None:
    db = SessionLocal()
    try:
        accounts = db.scalars(
            select(PlatformAccount).where(
                PlatformAccount.is_system.is_(False),
                PlatformAccount.status != "deleted",
            )
        ).all()
        for account in accounts:
            if account.credential is None:
                continue
            try:
                await check_account(account)
            except Exception as exc:  # noqa: BLE001 单个账号异常不中断整批巡检
                account.status, account.status_message = "error", f"体检失败: {exc}"
                account.last_checked_at = datetime.utcnow()
            db.add(
                CredentialAuditLog(
                    account_id=account.id, action="check", actor="scheduler", detail=account.status_message[:500]
                )
            )
            db.commit()
            logger.info("账号体检 #%s %s -> %s", account.id, account.platform, account.status)
    finally:
        db.close()


async def cleanup_browser_sessions() -> None:
    from app.modules.browser_qr import cleanup_expired

    await cleanup_expired()


async def cleanup_fetch_cache() -> None:
    """周期清理过期的抓取缓存行。"""
    from app.core import fetch_cache

    db = SessionLocal()
    try:
        removed = fetch_cache.purge_expired(db)
        db.commit()
        if removed:
            logger.info("清理过期抓取缓存 %s 行", removed)
    finally:
        db.close()


def start_scheduler() -> None:
    if settings.account_check_interval > 0:
        scheduler.add_job(check_all_accounts, "interval", minutes=settings.account_check_interval, id="account_check")
    scheduler.add_job(cleanup_browser_sessions, "interval", minutes=2, id="browser_gc")
    scheduler.add_job(cleanup_fetch_cache, "interval", minutes=30, id="fetch_cache_gc")
    scheduler.start()
    logger.info("调度器已启动，体检间隔 %s 分钟", settings.account_check_interval)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
