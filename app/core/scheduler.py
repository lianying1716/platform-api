"""定时任务：账号体检 + 浏览器扫码会话回收。"""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.core.crypto import decrypt_credential
from app.db import SessionLocal
from app.models import CredentialAuditLog, PlatformAccount

logger = logging.getLogger("platform.scheduler")
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def check_all_accounts() -> None:
    from app.modules import get_adapter

    db = SessionLocal()
    try:
        accounts = db.scalars(select(PlatformAccount)).all()
        for account in accounts:
            if account.credential is None:
                continue
            adapter = get_adapter(account.platform)
            try:
                result = await adapter.check(decrypt_credential(account.credential.ciphertext))
                account.status = "active" if result.ok else "expired"
                account.status_message = result.message
            except Exception as exc:
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


def start_scheduler() -> None:
    if settings.account_check_interval > 0:
        scheduler.add_job(check_all_accounts, "interval", minutes=settings.account_check_interval, id="account_check")
    scheduler.add_job(cleanup_browser_sessions, "interval", minutes=2, id="browser_gc")
    scheduler.start()
    logger.info("调度器已启动，体检间隔 %s 分钟", settings.account_check_interval)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
