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
            # 沉默期账号连体检都停（用户明确要求）：不做任何平台调用。
            if not bool(account.sync_enabled):
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


async def enqueue_due_subscriptions() -> None:
    """把到期的订阅同步入队（worker 消费），实现"开启即按 interval_minutes 周期同步"。

    到期判定：``enabled`` 且未删除，且距**上次尝试**超过 ``interval_minutes``。

    用 ``last_attempt_at`` 而不是 ``last_sync_at``：后者只在成功时更新，持续失败的订阅
    （账号掉线 / 平台风控）会被每次扫描重新入队，等于在风控期持续打平台。
    改用尝试时间后，失败也要等满一个 interval 才会再试（天然退避）。

    creator 订阅还要 ``sync_videos`` 打开（博主视频需手动开启）；收藏/点赞等自动同步类
    走各自的 ``enabled``。入队用 dedup，避免重复堆积。
    """
    from app.core.queue import enqueue
    from app.models import PlatformAccount, Subscription
    from app.services.account_state import is_callable

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        subs = db.scalars(
            select(Subscription)
            .join(PlatformAccount, PlatformAccount.id == Subscription.account_id)
            .where(
                Subscription.enabled.is_(True),
                Subscription.deleted_at.is_(None),
                # 停用/不可用账号的订阅不排队（排了也只会立刻报"账号已停用"）。
                PlatformAccount.status == "active",
                # 沉默期账号（同步总开关关闭）一律不排队：源头拦截，订阅开着也无效。
                PlatformAccount.sync_enabled.is_(True),
            )
        ).all()
        # 同步间隔：订阅自己设了 interval_minutes 就用它（固定值）；否则用设置页配置的
        # 全局随机区间（照抄抖小云"随机时间"的意图，避免固定整点节奏）。这里把随机放在
        # 判定这一层：每次到期后重新抽样，等价于"每次同步间隔都在区间内随机"。
        from app.core.settings_registry import resolve_subscription_interval
        from app.core.sync_settings import get_sync_settings

        sync_cfg = get_sync_settings(db)
        queued = 0
        for sub in subs:
            # 博主订阅：没开"同步博主视频"就只跟踪关注关系，不拉视频。
            if sub.source_type == "creator" and not sub.sync_videos:
                continue
            interval = resolve_subscription_interval(sub, sync_cfg)
            # 用「上次尝试」判到期：失败也退避满一个 interval，不在风控期反复打平台。
            last = sub.last_attempt_at or sub.last_sync_at
            if last is not None and (now - last).total_seconds() < interval * 60:
                continue
            enqueue(db, "sync", {"subscription_id": sub.id}, dedup=True, commit=False)
            queued += 1
        if queued:
            db.commit()
            logger.info("到期订阅入队 %s 个", queued)
    except Exception:  # noqa: BLE001 扫描失败不得让调度器崩
        logger.exception("订阅扫描异常")
    finally:
        db.close()


async def enqueue_due_crawl_tasks() -> None:
    """Enqueue enabled keyword-crawl tasks whose interval has elapsed.

    ``last_attempt_at`` advances on enqueue, not only on success, so failed
    tasks back off for a full interval instead of hammering the platform.
    """
    from app.application.commands.crawl import enqueue_crawl
    from app.models import CrawlTask, PlatformAccount
    from app.services.account_state import is_callable

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        tasks = db.scalars(
            select(CrawlTask).where(CrawlTask.enabled.is_(True))
        ).all()
        queued = 0
        for task in tasks:
            # A fixed account must still be callable; otherwise the task would
            # produce guaranteed failures.  Rotation tasks are checked in the
            # service, where a healthy account is selected.
            if task.account_id is not None:
                account = db.get(PlatformAccount, task.account_id)
                if account is None or not is_callable(account):
                    continue
            interval = max(10, int(task.interval_minutes or 1440))
            last = task.last_attempt_at or task.last_run_at
            if last is not None and (now - last).total_seconds() < interval * 60:
                continue
            job = enqueue_crawl(db, task.id, commit=False, dedup=True)
            if job.status not in ("queued", "running"):
                continue
            task.last_attempt_at = now
            queued += 1
        if queued:
            db.commit()
            logger.info("到期采集任务入队 %s 个", queued)
    except Exception:  # noqa: BLE001 扫描失败不得让调度器崩
        logger.exception("采集任务扫描异常")
    finally:
        db.close()


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
    if settings.subscription_scan_interval > 0:
        scheduler.add_job(
            enqueue_due_subscriptions,
            "interval",
            minutes=settings.subscription_scan_interval,
            id="subscription_scan",
        )
        scheduler.add_job(
            enqueue_due_crawl_tasks,
            "interval",
            minutes=settings.subscription_scan_interval,
            id="crawl_scan",
        )
    scheduler.add_job(cleanup_browser_sessions, "interval", minutes=2, id="browser_gc")
    scheduler.add_job(cleanup_fetch_cache, "interval", minutes=30, id="fetch_cache_gc")
    scheduler.start()
    logger.info("调度器已启动，体检间隔 %s 分钟", settings.account_check_interval)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
