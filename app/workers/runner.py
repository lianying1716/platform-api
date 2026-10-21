"""worker 主循环：轮询 PG 任务队列，按 job_type 分发给 handler。

独立进程运行：python -m app.workers.runner
- 单进程内可配多个并发槽（asyncio），下载/转录默认低并发避免风控与资源争抢。
- 领取任务用 queue.claim（FOR UPDATE SKIP LOCKED），多 worker 实例安全。
- 失败交给 queue.fail 处理重试；崩溃遗留的 running 任务由 reset_stale_running 回收。
"""

import asyncio
import inspect
import logging
import signal
from datetime import datetime

from app.config import settings
from app.core import queue
from app.db import SessionLocal
from app.domain.jobs import JobOutcome, RetryClass
from app.services import foundation
from app.services.asset_state import reconcile_job_terminal, reconcile_model_probe_terminal
from app.services.run_reconciler import reconcile_terminal_run
from app.workers.handlers import HANDLERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("platform.worker")

_stop = asyncio.Event()


def _log(db, job, level: str, message: str, detail: dict | None = None) -> None:
    """写一条结构化任务日志（claim/完成/失败）。

    增强项：日志写入失败只记日志，**绝不影响任务结果**。
    """
    try:
        foundation.log_task(db, job.id, job.job_type, level, message, detail=detail)
        db.commit()
    except Exception:  # noqa: BLE001 日志写入是增强项，失败不得翻转任务状态
        foundation.rollback_if_broken(db)
        logger.exception("任务日志写入失败，已忽略（不影响任务 #%s）", getattr(job, "id", None))


async def _run_one() -> bool:
    """领取并执行一个任务。返回 True 表示处理了任务，False 表示队列空。"""
    db = SessionLocal()
    try:
        job = queue.claim(db, job_types=list(HANDLERS.keys()))
        if job is None:
            return False
        _log(db, job, "info", f"领取任务 {job.job_type}")
        handler = HANDLERS.get(job.job_type)
        if handler is None:
            queue.fail(db, job, f"未知任务类型: {job.job_type}", retry_delay_seconds=0)
            _log(db, job, "error", f"未知任务类型: {job.job_type}")
            return True
        from app.services import workflow_engine

        workflow_engine.start_step(db, job)
        db.commit()
        logger.info("处理任务 #%s %s (第 %s 次)", job.id, job.job_type, job.attempts)
        heartbeat_task: asyncio.Task | None = None
        try:
            heartbeat_task = asyncio.create_task(_heartbeat_job(job.id))
            # 兼容签名在分发前判断，绝不用捕获 TypeError 再执行一次：
            # handler 内部真实的 TypeError 若被误判，会导致一次任务产生两次副作用。
            try:
                signature = inspect.signature(handler)
                accepts_job = len(signature.parameters) >= 3 or any(
                    p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                    for p in signature.parameters.values()
                )
            except (TypeError, ValueError):
                accepts_job = False
            if accepts_job:
                outcome = await handler(db, job.payload, job)
            else:
                outcome = await handler(db, job.payload)
        except queue.NonRetryableJobError as exc:
            logger.warning("任务 #%s 不可重试失败: %s", job.id, exc)
            queue.fail(db, job, str(exc), retry=False)
            _log(db, job, "error", f"不可重试失败: {exc}", detail={"error": str(exc)[:2000]})
            _reconcile_terminal(db, job)
        except Exception as exc:  # noqa: BLE001 任务失败不应让 worker 崩
            logger.exception("任务 #%s 失败", job.id)
            # A first retryable attempt is queued again; only the terminal
            # attempt is an operational alert.  Reconcile before logging so the
            # final attempt is not left in "queued" forever.
            queue.fail(db, job, str(exc))
            _log(db, job, "error", f"任务失败: {exc}", detail={"error": str(exc)[:2000]})
            _reconcile_terminal(db, job)
        else:
            if isinstance(outcome, JobOutcome) and not outcome.ok:
                message = outcome.message or outcome.code or "任务失败"
                queue.fail(
                    db,
                    job,
                    message,
                    retry=outcome.retry_allowed,
                retry_delay_seconds=0 if outcome.retry is RetryClass.RISK_COOLDOWN else 60,
                )
                _log(db, job, "error", message, detail={"code": outcome.code, "retry": str(outcome.retry)})
                _reconcile_terminal(db, job)
            else:
                queue.complete(db, job)
                step = workflow_engine.complete_step(db, job=job, failed=False)
                from app.services import alerts

                alerts.resolve_job_alerts(db, job.id)
                db.commit()
                # Assets processed before the DAG existed have no linked
                # workflow row.  They still need the same status projection
                # update; only the next-step release is workflow-specific.
                from app.services.asset_state import refresh_asset_after_step

                refresh_asset_after_step(db, job.job_type, job.payload)
                _log(db, job, "info", f"任务完成 {job.job_type}")
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
        return True
    finally:
        db.close()


async def _heartbeat_job(job_id: int) -> None:
    """Refresh job heartbeat while a handler is running, using its own session."""
    while True:
        await asyncio.sleep(30)
        db = SessionLocal()
        try:
            queue.heartbeat(db, job_id)
        except Exception:  # noqa: BLE001 心跳是增强项，数据库瞬断交给正式失败路径
            logger.warning("任务 #%s 心跳刷新失败", job_id, exc_info=True)
        finally:
            db.close()


def _reconcile_terminal(db, job) -> None:
    """Close the failed attempt; only terminal jobs fail asset/probe state.

    A retryable job goes back to ``queued``. Its failed domain run must still close,
    otherwise the next attempt creates a second run while the first stays ``running``.
    Asset/probe state stays in progress until the job itself reaches ``error``.
    """
    reconcile_terminal_run(db, job.job_type, job.payload, job_id=job.id)
    from app.services import workflow_engine

    if job.status == "error":
        workflow_engine.complete_step(db, job=job, failed=True, error=job.error or "任务失败")
        from app.services import alerts

        alerts.raise_job_failure(db, job, message=job.error or "任务失败")
        db.commit()
        from app.services.asset_state import refresh_asset_after_step

        refresh_asset_after_step(db, job.job_type, job.payload, failed=True, error=job.error)
        reconcile_job_terminal(db, job.job_type, job.payload, failed=True)
        if job.job_type == "probe_model":
            reconcile_model_probe_terminal(db, job.payload)
    else:
        # A retryable failure is still alive: the step remains queued, while
        # downstream steps stay blocked until this step actually succeeds.
        workflow_engine.retry_step(db, job)
        db.commit()


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
    """周期回收卡死的 running 任务，并把中断的素材状态拉回真实值。

    ``reset_stale_running`` 只处理任务行；素材行上的 ``downloading/transcribing/analyzing``
    是 handler 在干活前写下的，worker 崩溃后没人复位 → 前端永远显示"下载中"。
    这里一并对账（见 services/asset_state）。
    """
    from app.services.asset_state import clear_success_error, reset_stale_assets
    from app.services.run_reconciler import reconcile_runs
    from app.services.storage_lifecycle import recover_pending_deletions

    while not _stop.is_set():
        db = SessionLocal()
        try:
            n = queue.reset_stale_running(db, older_than_minutes=30)
            if n:
                logger.info("回收卡死任务 %s 个", n)
            runs = reconcile_runs(db, older_than_minutes=30)
            if runs:
                logger.info("对账卡死运行记录 %s", runs)
            fixed = reset_stale_assets(db, older_than_minutes=30)
            if fixed:
                logger.info("复位中断的素材状态 %s", fixed)
            cleaned = clear_success_error(db)
            if cleaned:
                logger.info("清理成功素材上的残留错误 %s 条", cleaned)
            recovered = recover_pending_deletions(db)
            if recovered:
                logger.info("恢复中断的素材删除任务 %s 个", recovered)
            from app.services.foundation import purge_old_task_logs

            # 无任务时也要留下存活证据：诊断页用最后心跳判断“worker 在但空闲”
            # 和“worker 根本没启动”，两者对用户是完全不同的处理动作。
            from datetime import datetime as _dt
            from app.models import SystemSetting as _Setting

            hb = db.get(_Setting, "worker_last_heartbeat")
            if hb is None:
                hb = _Setting(key="worker_last_heartbeat")
                db.add(hb)
            hb.value = _dt.utcnow().isoformat()
            db.commit()

            logs, purged_events, purged_windows = purge_old_task_logs(db, older_than_days=30)
            if logs:
                logger.info(
                    "清理过期任务日志 %s 条、事件 %s 条、限流窗口 %s 条",
                    logs,
                    purged_events,
                    purged_windows,
                )
        except Exception:  # noqa: BLE001
            logger.exception("janitor 异常")
        finally:
            db.close()
        await asyncio.sleep(120)


async def _startup_reconcile() -> None:
    """启动时先对账一次：worker 重启往往正是上一次崩溃的现场，早复位早可见。"""
    from app.services.asset_state import clear_success_error, reset_stale_assets
    from app.services.run_reconciler import reconcile_runs
    from app.services.storage_lifecycle import recover_pending_deletions

    from app.services.legacy_llm_migration import bootstrap_legacy_llm

    claim = bootstrap_legacy_llm()
    if claim is not None:
        logger.info("旧模型配置：%s", claim.message)

    db = SessionLocal()
    try:
        n = queue.reset_stale_running(db, older_than_minutes=30)
        if n:
            logger.info("启动回收卡死任务 %s 个", n)
        runs = reconcile_runs(db, older_than_minutes=30)
        if runs:
            logger.info("启动对账卡死运行记录 %s", runs)
        fixed = reset_stale_assets(db, older_than_minutes=30)
        if fixed:
            logger.info("启动复位中断的素材状态 %s", fixed)
        cleaned = clear_success_error(db)
        if cleaned:
            logger.info("启动清理成功素材上的残留错误 %s 条", cleaned)
        recovered = recover_pending_deletions(db)
        if recovered:
            logger.info("启动恢复中断的素材删除任务 %s 个", recovered)
        from app.models import SystemSetting

        hb = db.get(SystemSetting, "worker_last_heartbeat")
        if hb is None:
            hb = SystemSetting(key="worker_last_heartbeat")
            db.add(hb)
        hb.value = datetime.utcnow().isoformat()
        db.commit()
    except Exception:  # noqa: BLE001 启动对账失败不应阻止 worker 起来
        logger.exception("启动对账异常")
    finally:
        db.close()


async def main() -> None:
    concurrency = max(1, settings.worker_concurrency)
    logger.info("worker 启动，并发槽 %s", concurrency)
    await _startup_reconcile()

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
