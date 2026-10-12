"""升级编排引擎：备份 → 拉取 → 重建 → 健康校验 →（失败）自动回滚。

为什么放在 updater 边车而不是主 API：
升级过程会重建 platform-api 自身，主 API 进程随之退出，"进度/状态"必须存在一个
不会随升级重启的进程里——就是边车。前端轮询边车的 /upgrade/status 拿进度。

用户要求的关键保证（每一条都有对应测试）：
1. **备份是硬前置**：backup 失败立即中止，绝不无备份升级。
2. **健康校验**：重建后探测新版本；不健康则自动回滚到旧版本。
3. **并发锁**：同一时刻只允许一个升级。
4. **进度可查**：每步写入 job.steps，终态仍可查询。

依赖（backup/pull/recreate/health/rollback）全部注入，便于测试与复用。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("platform.updater.engine")


class UpgradeBusy(RuntimeError):
    """已有升级在进行中。"""


@dataclass
class UpgradeJob:
    version: str
    from_version: str = ""
    status: str = "running"  # running/success/failed/rolled_back
    error: str = ""
    backup_path: str = ""
    started_at: str = ""
    finished_at: str = ""
    steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "from_version": self.from_version,
            "status": self.status,
            "error": self.error,
            "backup_path": self.backup_path,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "steps": list(self.steps),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UpgradeEngine:
    """依赖注入式升级编排器（进程内单例，供边车使用）。"""

    def __init__(
        self, *, backup, pull, recreate, health, rollback, pin=None,
        health_retries=30, health_interval=3.0,
    ):
        self._backup = backup
        self._pull = pull
        self._recreate = recreate
        self._health = health
        self._rollback = rollback
        # pin：把目标版本持久化（写回 .env），否则重启后 compose 会按默认 tag
        # 静默切回旧版本，网页升级名存实亡。为 None 时跳过（兼容无持久化场景）。
        self._pin = pin
        self._health_retries = health_retries
        self._health_interval = health_interval
        self._lock = threading.Lock()
        self._job: UpgradeJob | None = None

    # ---- 状态查询 ----
    def latest(self) -> UpgradeJob | None:
        return self._job

    def is_busy(self) -> bool:
        return self._job is not None and self._job.status == "running"

    def _mark_running_for_test(self, version: str) -> None:
        """仅测试用：模拟一个进行中的任务以验证并发锁。"""
        self._job = UpgradeJob(version=version, status="running", started_at=_now())

    def _step(self, job: UpgradeJob, step: str, status: str, detail: str = "") -> None:
        job.steps.append({"step": step, "status": status, "detail": detail, "at": _now()})

    # ---- 编排 ----
    def start(self, version: str, *, from_version: str = "") -> UpgradeJob:
        """启动一次升级（同步执行，边车以线程方式调用）。已有进行中任务则抛 UpgradeBusy。"""
        with self._lock:
            if self.is_busy():
                raise UpgradeBusy(f"已有升级在进行中：{self._job.version}")
            job = UpgradeJob(version=version, from_version=from_version, started_at=_now())
            self._job = job
        try:
            self._run(job)
        except Exception as exc:  # noqa: BLE001 编排层兜底：任何异常都要落状态，不能静默
            logger.exception("升级编排异常")
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = _now()
        return job

    def run_sync(self, version: str, *, from_version: str = "") -> UpgradeJob:
        """测试/直接调用入口，等价 start。"""
        return self.start(version, from_version=from_version)

    def _run(self, job: UpgradeJob) -> None:
        # 1) 备份（硬前置）
        self._step(job, "backup", "running", "开始备份数据库与 data 卷")
        result = self._backup(job.version)
        if not result.get("ok"):
            job.status = "failed"
            job.error = f"备份失败，已中止升级：{result.get('error') or result}"
            self._step(job, "backup", "error", job.error)
            return
        job.backup_path = result.get("path", "")
        self._step(job, "backup", "ok", job.backup_path)

        # 2) 拉取新镜像
        self._step(job, "pull", "running", f"拉取 {job.version}")
        pull = self._pull(job.version)
        if not pull.get("ok"):
            self._abort_with_rollback(job, "pull", f"拉取镜像失败：{pull.get('error') or pull}")
            return
        self._step(job, "pull", "ok", "")

        # 3) 重建 api/worker
        self._step(job, "recreate", "running", "重建 platform-api / platform-worker")
        rec = self._recreate(job.version)
        if not rec.get("ok"):
            self._abort_with_rollback(job, "recreate", f"重建服务失败：{rec.get('error') or rec}")
            return
        self._step(job, "recreate", "ok", "")

        # 4) 健康校验（新版本是否真的起来了）
        self._step(job, "verify", "running", "健康校验新版本")
        ok = False
        for _ in range(self._health_retries):
            if self._health(job.version):
                ok = True
                break
            time.sleep(self._health_interval)
        if not ok:
            self._abort_with_rollback(job, "verify", "新版本健康校验超时")
            return
        self._step(job, "verify", "ok", "")

        # 5) 持久化版本（否则重启/compose up 会按默认 tag 静默回退）
        if self._pin is not None:
            self._step(job, "pin", "running", "记录当前版本到 .env")
            pr = self._pin(job.version)
            if not pr.get("ok"):
                # 记不上版本 = 重启就回退，等于升级没成功；按失败处理并回滚。
                self._abort_with_rollback(job, "pin", f"版本写入 .env 失败：{pr.get('error') or pr}")
                return
            self._step(job, "pin", "ok", "")

        job.status = "success"
        logger.info("升级成功：%s -> %s", job.from_version, job.version)

    def _abort_with_rollback(self, job: UpgradeJob, step: str, message: str) -> None:
        self._step(job, step, "error", message)
        self._step(job, "rollback", "running", "自动回滚到旧版本")
        rb = self._rollback(job.from_version or job.version)
        if rb.get("ok"):
            job.status = "rolled_back"
            job.error = f"{message}；已自动回滚"
            self._step(job, "rollback", "ok", "")
        else:
            job.status = "failed"
            job.error = f"{message}；回滚也失败：{rb.get('error') or rb}，请用备份手动恢复"
            self._step(job, "rollback", "error", job.error)
