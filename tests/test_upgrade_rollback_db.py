"""OPS-02: rollback must restore the DB; irreversible migrations must not auto-rollback."""

from app.updater.engine import UpgradeEngine
from app.updater import service as updater_service


def _engine(*, health, restore=None, backup_path="/backups/b.tar.gz"):
    calls = []

    def backup(version):
        calls.append(("backup", version))
        return {"ok": True, "path": backup_path}

    def rollback(version):
        calls.append(("rollback", version))
        return {"ok": True}

    def do_restore(path):
        calls.append(("restore", path))
        return {"ok": True}

    engine = UpgradeEngine(
        backup=backup,
        pull=lambda version: calls.append(("pull", version)) or {"ok": True},
        recreate=lambda version: calls.append(("recreate", version)) or {"ok": True},
        health=health,
        rollback=rollback,
        restore=do_restore if restore is None else restore,
        health_retries=1,
        health_interval=0.0,
    )
    return engine, calls


def test_failed_upgrade_restores_database_before_switching_image():
    engine, calls = _engine(health=lambda version: {"reachable": True, "app_version": "old", "db_in_sync": True})
    job = engine.run_sync("v2.0.0", from_version="v1.0.0")
    assert job.status == "rolled_back"
    assert calls.index(("restore", "/backups/b.tar.gz")) < calls.index(("rollback", "v1.0.0")), calls
    assert any(step["step"] == "db_restore" and step["status"] == "ok" for step in job.steps)


def test_restore_failure_does_not_claim_a_rollback():
    engine, calls = _engine(
        health=lambda version: {"reachable": False},
        restore=lambda path: {"ok": False, "error": "disk full"},
    )
    job = engine.run_sync("v2.0.0", from_version="v1.0.0")
    assert job.status == "failed"
    assert "数据库恢复失败" in job.error
    assert ("rollback", "v1.0.0") not in calls


def test_irreversible_migration_blocks_automatic_rollback():
    engine, calls = _engine(health=lambda version: {"reachable": False})
    job = engine.run_sync("v2.0.0", from_version="v1.0.0", reversible=False)
    assert job.status == "failed"
    assert "不可逆" in job.error
    assert ("rollback", "v1.0.0") not in calls
    assert not any(step["step"] == "db_restore" for step in job.steps)


def test_restore_requires_a_backup_path():
    engine, calls = _engine(health=lambda version: {"reachable": False}, backup_path="")
    job = engine.run_sync("v2.0.0", from_version="v1.0.0")
    assert job.status == "failed"
    assert "无备份" in job.error
    assert ("rollback", "v1.0.0") not in calls
