"""Runtime capability checks are deployment-agnostic and distinguish optional tools."""


def test_transcription_capability_reports_missing_dependency(monkeypatch):
    import app.core.runtime_capabilities as runtime

    monkeypatch.setattr(runtime, "_module_available", lambda name: name != "faster_whisper")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    rows = {row.key: row for row in runtime.runtime_capabilities()}

    assert rows["transcription"].available is False
    assert rows["transcription"].required is True
    assert "faster-whisper" in rows["transcription"].detail
    assert "transcribe" in rows["transcription"].fix


def test_transcription_capability_detects_unimportable_stack(monkeypatch):
    """Package metadata alone is not proof: av/faster-whisper may be incompatible."""
    import app.core.runtime_capabilities as runtime

    monkeypatch.setattr(runtime, "_module_available", lambda name: True)
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(runtime, "_transcription_stack_error", lambda: "TypeError: metadata_errors")
    row = runtime.runtime_capabilities()[0]
    assert row.available is False
    assert "metadata_errors" in row.detail


def test_diagnostics_separates_required_and_optional_failures(client, session, monkeypatch):
    import app.core.runtime_capabilities as runtime
    from app.modules import browser_runtime

    monkeypatch.setattr(
        runtime,
        "runtime_capabilities",
        lambda: [
            runtime.RuntimeCapability(
                key="transcription",
                label="语音转录",
                available=True,
                required=True,
                detail="就绪",
                fix="",
            )
        ],
    )
    monkeypatch.setattr(
        browser_runtime,
        "status",
        lambda: {"playwright": True, "chromium": True, "node_available": False},
    )
    from app.api import system as system_api

    class _Scheduler:
        running = True

    monkeypatch.setattr(system_api, "scheduler", _Scheduler())
    from datetime import datetime

    from app.models import SystemSetting

    session.add(SystemSetting(key="worker_last_heartbeat", value=datetime.utcnow().isoformat()))
    session.commit()
    body = client.get("/api/system/diagnostics").json()
    checks = {row["name"]: row for row in body["checks"]}

    assert checks["Node"]["required"] is False
    assert body["required_ok"] is True
    assert "Node" in body["optional_failures"]
    assert body["all_ok"] is True


def test_diagnostics_requires_worker_heartbeat_even_when_queue_is_empty(client, session):
    """An idle queue must not mask an API-only install.

    The common failure mode is a healthy web server with no worker process; in
    that state the queue is empty until the first user action, so a queue-count
    check alone reports a false green.
    """
    from app.models import SystemSetting

    session.query(SystemSetting).filter_by(key="worker_last_heartbeat").delete()
    session.commit()

    body = client.get("/api/system/diagnostics").json()
    row = next(c for c in body["checks"] if c["name"] == "任务执行器")
    assert row["ok"] is False
    assert row["required"] is True
    assert "未检测到 worker 心跳" in row["detail"]


def test_diagnostics_accepts_fresh_worker_heartbeat(client, session):
    from datetime import datetime

    from app.models import SystemSetting

    row = session.get(SystemSetting, "worker_last_heartbeat")
    if row is None:
        row = SystemSetting(key="worker_last_heartbeat")
        session.add(row)
    row.value = datetime.utcnow().isoformat()
    session.commit()

    body = client.get("/api/system/diagnostics").json()
    task = next(c for c in body["checks"] if c["name"] == "任务执行器")
    assert task["ok"] is True
    assert "最后心跳" in task["detail"]
