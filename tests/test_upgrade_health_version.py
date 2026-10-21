"""OPS-01: a 200 must not pass unless it is the target version with a synced DB."""

from app.updater.engine import UpgradeEngine, health_is_target
from app.updater import service as updater_service


def test_health_rejects_old_version_even_when_reachable():
    assert health_is_target(
        {"reachable": True, "app_version": "v0.1.0", "db_in_sync": True}, "v0.2.0"
    ) is False


def test_health_rejects_unsynced_database():
    assert health_is_target(
        {"reachable": True, "app_version": "v0.2.0", "db_in_sync": False}, "v0.2.0"
    ) is False


def test_health_accepts_exact_target_and_sync():
    assert health_is_target(
        {"reachable": True, "app_version": "v0.2.0", "db_in_sync": True}, "v0.2.0"
    ) is True


def test_health_rejects_same_version_with_wrong_commit():
    probe = {
        "reachable": True,
        "app_version": "v0.2.0",
        "git_commit": "deadbeef",
        "db_in_sync": True,
    }
    assert health_is_target(probe, "v0.2.0", "cafebabe") is False
    assert health_is_target(probe, "v0.2.0", "deadbeef") is True
    assert health_is_target(probe, "v0.2.0", "") is True


def test_health_never_accepts_unknown_or_non_sha_commit_as_verified():
    probe = {
        "reachable": True,
        "app_version": "v0.2.0",
        "git_commit": "unknown",
        "db_in_sync": True,
    }
    assert health_is_target(probe, "v0.2.0", "cafebabe") is False
    assert health_is_target({**probe, "git_commit": "cafebabe"}, "v0.2.0", "unknown") is False


def test_engine_rolls_back_when_endpoint_is_old_version():
    calls = []

    def health(version):
        calls.append(("health", version))
        # Reachable, but still serving the previous version.
        return {"reachable": True, "app_version": "v0.1.0", "db_in_sync": True}

    engine = UpgradeEngine(
        backup=lambda v: {"ok": True, "path": "/b"},
        pull=lambda v: {"ok": True},
        recreate=lambda v: {"ok": True},
        health=health,
        rollback=lambda v: calls.append(("rollback", v)) or {"ok": True},
        health_retries=2,
        health_interval=0.0,
    )
    job = engine.run_sync("v0.2.0", from_version="v0.1.0")
    assert job.status == "rolled_back"
    assert ("rollback", "v0.1.0") in calls
    assert "v0.1.0" in job.error and "v0.2.0" in job.error


def test_engine_rolls_back_when_same_version_has_wrong_commit():
    calls = []

    def health(version):
        calls.append(("health", version))
        return {
            "reachable": True,
            "app_version": "v0.2.0",
            "git_commit": "deadbeef",
            "db_in_sync": True,
        }

    engine = UpgradeEngine(
        backup=lambda v: {"ok": True, "path": "/b"},
        pull=lambda v: {"ok": True},
        recreate=lambda v: {"ok": True},
        health=health,
        rollback=lambda v: calls.append(("rollback", v)) or {"ok": True},
        health_retries=1,
        health_interval=0.0,
    )
    job = engine.run_sync("v0.2.0", from_version="v0.1.0", expected_commit="cafebabe")
    assert job.status == "rolled_back"
    assert job.expected_commit == "cafebabe"
    assert ("rollback", "v0.1.0") in calls


def test_engine_marks_commit_verified_only_when_expected_commit_was_checked():
    def engine_for(expected: str):
        return UpgradeEngine(
            backup=lambda v: {"ok": True, "path": "/b"},
            pull=lambda v: {"ok": True},
            recreate=lambda v: {"ok": True},
            health=lambda v: {
                "reachable": True,
                "app_version": "v0.2.0",
                "git_commit": "cafebabe",
                "db_in_sync": True,
            },
            rollback=lambda v: {"ok": True},
            health_retries=1,
            health_interval=0.0,
        ), expected

    checked, expected = engine_for("cafebabe")
    assert checked.run_sync("v0.2.0", expected_commit=expected).commit_verified is True
    unchecked, _ = engine_for("")
    assert unchecked.run_sync("v0.2.0").commit_verified is False


def test_real_probe_parses_structured_endpoint(monkeypatch):
    import io
    import json

    class Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = json.dumps(
        {"app_version": "v0.2.0", "db_in_sync": True, "db_revision": "0030", "db_head": "0030"}
    ).encode()
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resp(payload))
    probe = updater_service._do_health("v0.2.0")
    assert probe["app_version"] == "v0.2.0"
    assert probe["db_in_sync"] is True
    assert probe["target_version"] == "v0.2.0"
