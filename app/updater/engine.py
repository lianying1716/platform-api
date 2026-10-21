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
import re
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
    # partially_rolled_back = the old image and DB are back, but the data volume
    # could not be restored.  It is deliberately distinct from rolled_back.
    status: str = "running"  # running/success/failed/rolled_back/partially_rolled_back
    error: str = ""
    backup_path: str = ""
    # Whether the target migration can be rolled back safely.  Unknown/irreversible
    # upgrades must not auto-rollback; they need a deliberate restore decision.
    reversible: bool = True
    expected_commit: str = ""
    commit_verified: bool = False
    db_restored: bool = False
    data_restored: bool = False
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
            "reversible": self.reversible,
            "expected_commit": self.expected_commit,
            "commit_verified": self.commit_verified,
            "db_restored": self.db_restored,
            "data_restored": self.data_restored,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "steps": list(self.steps),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def health_is_target(probe, version: str, expected_commit: str = "") -> bool:
    """Decide whether a health probe proves *the target* version is serving.

    Accepts a structured dict from the real updater (preferred) and a plain
    bool for older injected probes/tests.  A 200 from the old container must
    never pass: the version and DB revision are part of the contract.
    """
    if isinstance(probe, bool):
        return probe
    if not isinstance(probe, dict):
        return False
    if not probe.get("reachable", True):
        return False
    version_ok = (
        str(probe.get("app_version") or "") == str(version)
        and probe.get("db_in_sync") is True
    )
    if not version_ok:
        return False
    if not expected_commit:
        return True
    actual = str(probe.get("git_commit") or "").strip()
    expected = str(expected_commit).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", actual):
        return False
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", expected):
        return False
    # Short SHAs are accepted in either direction, but the actual commit must
    # be present.  A missing commit never proves a same-named release.
    return bool(actual) and (actual.startswith(expected[:12]) or expected.startswith(actual[:12]))


class UpgradeEngine:
    """依赖注入式升级编排器（进程内单例，供边车使用）。"""

    def __init__(
        self, *, backup, pull, recreate, health, rollback, pin=None,
        restore=None, stop=None, prepare_rollback=None,
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
        # restore(backup_path)：可逆升级失败时恢复数据库与 data 卷。
        # 返回 db_restored / data_restored 两个独立事实；只有两者都为真，
        # 且旧镜像重建成功，终态才是 rolled_back。
        # 未注入时回滚只切镜像，调用方必须显式接受这一限制。
        self._restore = restore
        # stop(*services)：在恢复数据库/数据卷前停止新容器，避免恢复期间仍有
        # API/worker 写同一卷。未注入时保留旧行为；生产边车必须注入。
        self._stop = stop or (lambda *services: {"ok": True})
        # prepare_rollback(version)：在停止服务前确认旧镜像已在本机可用
        # （只 create，不 start）。避免“先停新服务，随后才发现旧镜像拉不到”
        # 把可恢复的失败变成整站不可用。
        self._prepare_rollback = prepare_rollback or (lambda version: {"ok": True})
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
    def start(
        self, version: str, *, from_version: str = "", reversible: bool = True,
        expected_commit: str = "",
    ) -> UpgradeJob:
        """启动一次升级（同步执行，边车以线程方式调用）。已有进行中任务则抛 UpgradeBusy。"""
        job = self.claim(version, from_version=from_version, reversible=reversible, expected_commit=expected_commit)
        return self.execute(job)

    def claim(
        self, version: str, *, from_version: str = "", reversible: bool = True,
        expected_commit: str = "",
    ) -> UpgradeJob:
        """Atomically reserve the single upgrade slot before returning accepted.

        Checking ``is_busy`` in the HTTP handler and starting a thread later is
        racy: two requests can both return 202 while only one thread wins the
        engine lock.  The loser then dies unseen in the background.  Claiming
        the job while holding the lock lets the endpoint reject that request.
        """
        with self._lock:
            if self.is_busy():
                raise UpgradeBusy(f"已有升级在进行中：{self._job.version}")
            job = UpgradeJob(
                version=version,
                from_version=from_version,
                reversible=reversible,
                expected_commit=expected_commit,
                started_at=_now(),
            )
            self._job = job
            return job

    def execute(self, job: UpgradeJob) -> UpgradeJob:
        """Run a previously claimed job; this is the thread body."""
        try:
            self._run(job)
        except Exception as exc:  # noqa: BLE001 编排层兜底：任何异常都要落状态，不能静默
            logger.exception("升级编排异常")
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = _now()
        return job

    def run_sync(
        self, version: str, *, from_version: str = "", reversible: bool = True,
        expected_commit: str = "",
    ) -> UpgradeJob:
        """测试/直接调用入口，等价 start。"""
        return self.start(
            version, from_version=from_version, reversible=reversible,
            expected_commit=expected_commit,
        )

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
            self._abort_with_rollback(
                job, "pull", f"拉取镜像失败：{pull.get('error') or pull}", restore_db=False
            )
            return
        self._step(job, "pull", "ok", "")

        # 3) 重建 api/worker
        self._step(job, "recreate", "running", "重建 platform-api / platform-worker")
        rec = self._recreate(job.version)
        if not rec.get("ok"):
            self._abort_with_rollback(
                job, "recreate", f"重建服务失败：{rec.get('error') or rec}", restore_db=False
            )
            return
        self._step(job, "recreate", "ok", "")

        # 4) 健康校验（新版本是否真的起来了）
        self._step(job, "verify", "running", "健康校验新版本")
        ok = False
        last_probe = None
        for _ in range(self._health_retries):
            last_probe = self._health(job.version)
            if health_is_target(last_probe, job.version, job.expected_commit):
                ok = True
                break
            time.sleep(self._health_interval)
        if not ok:
            detail = last_probe if isinstance(last_probe, dict) else {"healthy": last_probe}
            self._abort_with_rollback(
                job,
                "verify",
                f"新版本健康校验超时：目标 {job.version}，探测结果 {detail}",
            )
            return
        self._step(job, "verify", "ok", "")
        job.commit_verified = bool(job.expected_commit)

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

    def _abort_with_rollback(
        self, job: UpgradeJob, step: str, message: str, *, restore_db: bool = True
    ) -> None:
        self._step(job, step, "error", message)
        if not job.reversible:
            job.status = "failed"
            job.error = f"{message}；迁移不可逆，已阻止自动回滚，请按备份人工恢复"
            self._step(job, "rollback", "error", job.error)
            return
        self._step(job, "rollback", "running", "自动回滚到旧版本")
        will_restore = restore_db and self._restore is not None
        # New containers may still be writing the data volume.  Before the
        # restore swaps /data, stop the upgraded services; the rollback step
        # below brings the old API (and, for a complete restore, the worker)
        # back up.
        if will_restore:
            if not job.backup_path:
                job.status = "failed"
                job.error = f"{message}；无备份可恢复，已阻止自动回滚，请人工处理"
                self._step(job, "rollback", "error", job.error)
                return
            prepared = self._prepare_rollback(job.from_version or job.version)
            if not prepared.get("ok"):
                job.status = "failed"
                job.error = f"{message}；旧镜像不可用，未停止服务、未改动备份：{prepared.get('error') or prepared}"
                self._step(job, "rollback", "error", job.error)
                return
            stop = self._stop("platform-api", "platform-worker")
            if not stop.get("ok"):
                job.status = "failed"
                job.error = f"{message}；停止新服务失败：{stop.get('error') or stop}，未改动备份，请人工处理"
                self._step(job, "rollback", "error", job.error)
                return
        # Restore the DB first: running the old image against a schema that the
        # new version already migrated can fail to boot.  The data volume must
        # be restored as part of the same transaction: DB rows point at covers,
        # media and the credential key stored there.  Any split state is
        # reported as partial, never as a complete rollback.
        # Pull failures happen before any new container starts, and a failed
        # recreate may not have started one either.  Restoring the pre-upgrade
        # dump in those cases would roll back writes made by the still-running
        # old service.  Only restore after the new image could have migrated.
        if will_restore:
            rest = self._restore(job.backup_path)
            job.db_restored = bool(rest.get("db_restored"))
            job.data_restored = bool(rest.get("data_restored"))
            if not job.db_restored:
                job.status = "failed"
                job.error = f"{message}；升级前备份恢复失败：{rest.get('error') or rest}，请人工处理"
                self._step(job, "rollback", "error", job.error)
                return
            self._step(job, "db_restore", "ok", job.backup_path)
            if job.data_restored:
                self._step(job, "data_restore", "ok", job.backup_path)
            else:
                self._step(
                    job,
                    "data_restore",
                    "error",
                    f"data 卷恢复失败：{rest.get('error') or rest}",
                )
        # A partial restore must not start the worker: it would immediately
        # write new jobs/files into the half-restored volume.  Bring the API
        # back so the operator can inspect status, but leave the worker down
        # until the data volume has been repaired from the same backup.
        partial = will_restore and not job.data_restored
        # A full restore has already stopped both services; recreate them with
        # the old image.  A partial restore brings back only the API so the
        # worker cannot write into an incompletely restored data volume.
        rb = (
            self._rollback(job.from_version or job.version, api_only=True)
            if partial
            else self._rollback(job.from_version or job.version)
        )
        if rb.get("ok"):
            if will_restore and not job.data_restored:
                job.status = "partially_rolled_back"
                job.error = (
                    f"{message}；数据库与旧镜像已恢复，但 data 卷仍未恢复："
                    f"{rest.get('error') or rest}。请在维护窗口用同一备份人工恢复 data 卷后再启用 worker"
                )
                self._step(job, "rollback", "error", job.error)
            else:
                job.status = "rolled_back"
                job.error = f"{message}；已自动回滚"
                self._step(job, "rollback", "ok", "")
        else:
            job.status = "failed"
            job.error = f"{message}；回滚也失败：{rb.get('error') or rb}，请用备份手动恢复"
            self._step(job, "rollback", "error", job.error)
