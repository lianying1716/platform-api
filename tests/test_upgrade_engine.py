"""升级编排引擎（全套）：备份→拉取→重建→健康校验→失败自动回滚。

用注入的假依赖测试状态机，不碰 docker / 真库。
覆盖用户要求的关键保证：
- 备份是硬前置，失败即中止（绝不无备份升级）
- 升级后健康校验，新版本未起来 → 自动回滚到旧版本
- 同一时刻只允许一个升级（并发锁）
- 全程进度/日志可查（因为 API 会重启，状态存在边车里）
"""

import pytest

from app.updater import engine as eng


class _Recorder:
    """记录调用顺序，供断言"先备份、再拉取…"等顺序契约。"""

    def __init__(self, *, backup_ok=True, health_ok=True, recreate_ok=True, pull_ok=True):
        self.calls = []
        self.backup_ok = backup_ok
        self.health_ok = health_ok
        self.recreate_ok = recreate_ok
        self.pull_ok = pull_ok

    def backup(self, version):
        self.calls.append(("backup", version))
        return {"ok": self.backup_ok, "path": f"/backups/{version}.tar.gz"}

    def pull(self, version):
        self.calls.append(("pull", version))
        return {"ok": self.pull_ok}

    def recreate(self, version):
        self.calls.append(("recreate", version))
        return {"ok": self.recreate_ok}

    def health(self, version):
        self.calls.append(("health", version))
        return self.health_ok

    def rollback(self, version):
        self.calls.append(("rollback", version))
        return {"ok": True}


def _engine(rec):
    return eng.UpgradeEngine(
        backup=rec.backup, pull=rec.pull, recreate=rec.recreate, health=rec.health,
        rollback=rec.rollback, health_retries=2, health_interval=0.01,
    )


def test_happy_path_order_and_success():
    """成功路径：备份→拉取→重建→健康校验，顺序正确，终态 success。"""
    rec = _Recorder()
    e = _engine(rec)
    job = e.run_sync("v1.2.0")
    assert [c[0] for c in rec.calls] == ["backup", "pull", "recreate", "health"]
    assert job.status == "success"
    assert job.version == "v1.2.0"


def test_backup_failure_aborts_before_touching_images():
    """备份失败：立即中止，绝不拉取/重建（硬前置）。"""
    rec = _Recorder(backup_ok=False)
    e = _engine(rec)
    job = e.run_sync("v1.2.0")
    assert [c[0] for c in rec.calls] == ["backup"]
    assert job.status == "failed"
    assert "备份" in job.error


def test_unhealthy_after_upgrade_triggers_rollback():
    """新版本健康校验失败：自动回滚到旧版本，终态 rolled_back。"""
    rec = _Recorder(health_ok=False)
    e = _engine(rec)
    job = e.run_sync("v1.2.0")
    assert ("rollback", "v1.2.0") in rec.calls
    assert job.status == "rolled_back"
    assert rec.calls[-1][0] == "rollback"


def test_pull_failure_triggers_rollback():
    """拉取失败也要回滚（此时已备份，确保回到可用状态）。"""
    rec = _Recorder(pull_ok=False)
    e = _engine(rec)
    job = e.run_sync("v1.2.0")
    assert job.status in ("rolled_back", "failed")
    assert ("rollback", "v1.2.0") in rec.calls


def test_only_one_upgrade_at_a_time():
    """并发锁：已有进行中的升级时，第二次被拒绝。"""
    rec = _Recorder()
    e = _engine(rec)
    e._mark_running_for_test("v1.0.0")
    with pytest.raises(eng.UpgradeBusy):
        e.start("v1.2.0")


def test_status_progression_is_recorded():
    """进度可查：每个步骤都应写入 job.steps。"""
    rec = _Recorder()
    e = _engine(rec)
    job = e.run_sync("v1.2.0")
    steps = [s["step"] for s in job.steps]
    assert "backup" in steps and "pull" in steps and "recreate" in steps and "verify" in steps


def test_latest_job_queryable_after_completion():
    """升级完成后仍能查到最近一次 job（供前端轮询）。"""
    rec = _Recorder()
    e = _engine(rec)
    e.run_sync("v1.2.0")
    latest = e.latest()
    assert latest is not None and latest.version == "v1.2.0"


def test_pin_persists_version_after_healthy_upgrade():
    """健康校验通过后必须把版本写回 .env（持久化），否则重启会静默回退。"""
    rec = _Recorder()
    pinned = {}

    def _pin(version):
        pinned["v"] = version
        return {"ok": True}

    e = eng.UpgradeEngine(
        backup=rec.backup, pull=rec.pull, recreate=rec.recreate, health=rec.health,
        rollback=rec.rollback, pin=_pin, health_retries=2, health_interval=0.01,
    )
    job = e.run_sync("v1.3.0")
    assert job.status == "success"
    assert pinned["v"] == "v1.3.0", "升级成功后必须 pin 版本"


def test_pin_failure_fails_the_upgrade():
    """pin 失败 = 升级失败（否则重启会回退，等于没升成功）。"""
    rec = _Recorder()

    def _pin(version):
        return {"ok": False, "error": "磁盘只读"}

    e = eng.UpgradeEngine(
        backup=rec.backup, pull=rec.pull, recreate=rec.recreate, health=rec.health,
        rollback=rec.rollback, pin=_pin, health_retries=2, health_interval=0.01,
    )
    job = e.run_sync("v1.3.0")
    assert job.status in ("failed", "rolled_back")
    assert "pin" in job.error.lower() or "版本" in job.error or "写入" in job.error


def test_pin_not_called_when_unhealthy():
    """健康校验失败时不得 pin（不能把坏版本记成当前版本）。"""
    rec = _Recorder(health_ok=False)
    called = {"pin": False}

    def _pin(version):
        called["pin"] = True
        return {"ok": True}

    e = eng.UpgradeEngine(
        backup=rec.backup, pull=rec.pull, recreate=rec.recreate, health=rec.health,
        rollback=rec.rollback, pin=_pin, health_retries=2, health_interval=0.01,
    )
    e.run_sync("v1.3.0")
    assert called["pin"] is False
