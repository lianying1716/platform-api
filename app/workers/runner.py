"""worker 主循环：轮询 PG 任务队列，按 job_type 分发给 handler。

独立进程运行：python -m app.workers.runner
- 单进程内可配多个并发槽（asyncio），下载/转录默认低并发避免风控与资源争抢。
- 领取任务用 queue.claim（FOR UPDATE SKIP LOCKED），多 worker 实例安全。
- 失败交给 queue.fail 处理重试；崩溃遗留的 running 任务由 reset_stale_running 回收。
"""

import asyncio
import logging
import signal

from app.config import settings
from app.core import queue
from app.db import SessionLocal
from app.workers.handlers import HANDLERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("platform.worker")

_stop = asyncio.Event()


async def _run_one() -> bool:
    """领取并执行一个任务。返回 True 表示处理了任务，False 表示队列空。"""
    db = SessionLocal()
    try:
        job = queue.claim(db, job_types=list(HANDLERS.keys()))
        if job is None:
            return False
        handler = HANDLERS.get(job.job_type)
        if handler is None:
            queue.fail(db, job, f"未知任务类型: {job.job_type}", retry_delay_seconds=0)
            return True
        logger.info("处理任务 #%s %s (第 %s 次)", job.id, job.job_type, job.attempts)
        try:
            await handler(db, job.payload)
        except queue.NonRetryableJobError as exc:
            logger.warning("任务 #%s 不可重试失败: %s", job.id, exc)
            queue.fail(db, job, str(exc), retry=False)
        except Exception as exc:  # noqa: BLE001 任务失败不应让 worker 崩
            logger.exception("任务 #%s 失败", job.id)
            queue.fail(db, job, str(exc))
        else:
            queue.complete(db, job)
        return True
    finally:
        db.close()


async def _worker_loop(slot: int) -> None:
    idle_sleep = 2.0
    while not _stop.is_set():
        try:
            did = await _run_one()
        except Exception:  # noqa: BLE001 数据库瞬断等，歇一下重试
            logger.exception("worker 槽 #%s 循环异常", slot)
            did = False
        await asyncio.sleep(0 if did else idle_sleep)


async def _janitor() -> None:
    """周期回收卡死的 running 任务。"""
    while not _stop.is_set():
        db = SessionLocal()
        try:
            n = queue.reset_stale_running(db, older_than_minutes=30)
            if n:
                logger.info("回收卡死任务 %s 个", n)
        except Exception:  # noqa: BLE001
            logger.exception("janitor 异常")
        finally:
            db.close()
        await asyncio.sleep(120)


async def main() -> None:
    concurrency = max(1, settings.worker_concurrency)
    logger.info("worker 启动，并发槽 %s", concurrency)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop.set)
        except NotImplementedError:
            pass

    tasks = [asyncio.create_task(_worker_loop(i)) for i in range(concurrency)]
    tasks.append(asyncio.create_task(_janitor()))
    await _stop.wait()
    logger.info("收到停止信号，等待当前任务结束…")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
