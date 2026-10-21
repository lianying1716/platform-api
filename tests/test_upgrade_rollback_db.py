"""OPS-02/OPS-16: rollback restores both DB and data, or says it is partial."""

from app.updater.engine import UpgradeEngine
from app.updater import service as updater_service


def _engine(*, health, restore=None, backup_path="/backups/b.tar.gz", pull=None):
    calls = []

    def backup(version):
        calls.append(("backup", version))
        return {"ok": True, "path": backup_path}

    def rollback(version, *, api_only=False):
        calls.append(("rollback", version, api_only))
        return {"ok": True}

    def stop(*services):
        calls.append(("stop", *services))
        return {"ok": True}

    def prepare_rollback(version):
        calls.append(("prepare", version))
        return {"ok": True}

    def do_restore(path):
        calls.append(("restore", path))
        return {"ok": True, "db_restored": True, "data_restored": True}

    engine = UpgradeEngine(
        backup=backup,
        pull=pull or (lambda version: calls.append(("pull", version)) or {"ok": True}),
        recreate=lambda version: calls.append(("recreate", version)) or {"ok": True},
        health=health,
        rollback=rollback,
        stop=stop,
        prepare_rollback=prepare_rollback,
        restore=do_restore if restore is None else restore,
        health_retries=1,
        health_interval=0.0,
    )
    return engine, calls


def test_failed_upgrade_restores_database_before_switching_image():
    engine, calls = _engine(health=lambda version: {"reachable": True, "app_version": "old", "db_in_sync": True})
    job = engine.run_sync("v2.0.0", from_version="v1.0.0")
    assert job.status == "rolled_back"
    assert calls.index(("prepare", "v1.0.0")) < calls.index(("stop", "platform-api", "platform-worker")) < calls.index(("restore", "/backups/b.tar.gz")), calls
    assert any(step["step"] == "db_restore" and step["status"] == "ok" for step in job.steps)
    assert any(step["step"] == "data_restore" and step["status"] == "ok" for step in job.steps)
    assert job.db_restored is True and job.data_restored is True


def test_pull_failure_does_not_restore_database_before_new_image_starts():
    """A failed pull cannot have migrated; restoring would discard live writes."""
    engine, calls = _engine(
        health=lambda version: {"reachable": False},
        pull=lambda version: {"ok": False, "error": "registry down"},
    )
    job = engine.run_sync("v2.0.0", from_version="v1.0.0")
    assert job.status == "rolled_back"
    assert ("restore", "/backups/b.tar.gz") not in calls
    assert ("rollback", "v1.0.0", False) in calls


def test_restore_failure_does_not_claim_a_rollback():
    engine, calls = _engine(
        health=lambda version: {"reachable": False},
        restore=lambda path: {"ok": False, "db_restored": False, "data_restored": False, "error": "disk full"},
    )
    job = engine.run_sync("v2.0.0", from_version="v1.0.0")
    assert job.status == "failed"
    assert "备份恢复失败" in job.error
    assert ("rollback", "v1.0.0", False) not in calls


def test_irreversible_migration_blocks_automatic_rollback():
    engine, calls = _engine(health=lambda version: {"reachable": False})
    job = engine.run_sync("v2.0.0", from_version="v1.0.0", reversible=False)
    assert job.status == "failed"
    assert "不可逆" in job.error
    assert ("rollback", "v1.0.0", False) not in calls
    assert not any(step["step"] == "db_restore" for step in job.steps)


def test_restore_requires_a_backup_path():
    engine, calls = _engine(health=lambda version: {"reachable": False}, backup_path="")
    job = engine.run_sync("v2.0.0", from_version="v1.0.0")
    assert job.status == "failed"
    assert "无备份" in job.error
    assert ("rollback", "v1.0.0", False) not in calls


def test_data_restore_failure_is_reported_as_partial_rollback():
    """DB + old image may be back, but missing files must not look complete."""
    engine, calls = _engine(
        health=lambda version: {"reachable": False},
        restore=lambda path: {
            "ok": False, "db_restored": True, "data_restored": False,
            "error": "data 卷恢复失败：no space left",
        },
    )
    job = engine.run_sync("v2.0.0", from_version="v1.0.0")
    assert job.status == "partially_rolled_back"
    assert job.db_restored is True and job.data_restored is False
    assert "data 卷仍未恢复" in job.error
    assert "no space left" in job.error
    assert ("rollback", "v1.0.0", True) in calls
    assert any(step["step"] == "data_restore" and step["status"] == "error" for step in job.steps)


def test_rollback_preflight_failure_does_not_stop_or_touch_live_services():
    """A missing old image must leave the running upgrade untouched."""
    calls = []

    def prepare(_version):
        calls.append("prepare")
        return {"ok": False, "error": "manifest unknown"}

    def stop(*_services):
        calls.append("stop")
        return {"ok": True}

    def restore(_path):
        calls.append("restore")
        return {"ok": True, "db_restored": True, "data_restored": True}

    engine = UpgradeEngine(
        backup=lambda _v: {"ok": True, "path": "/b"},
        pull=lambda _v: {"ok": True},
        recreate=lambda _v: {"ok": True},
        health=lambda _v: {"reachable": False},
        rollback=lambda _v, api_only=False: calls.append("rollback") or {"ok": True},
        stop=stop,
        restore=restore,
        prepare_rollback=prepare,
        health_retries=1,
        health_interval=0,
    )
    job = engine.run_sync("v2.0.0", from_version="v1.0.0")
    assert job.status == "failed"
    assert "旧镜像不可用" in job.error
    assert calls == ["prepare"]
