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


def test_diagnostics_separates_required_and_optional_failures(client, monkeypatch):
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
    body = client.get("/api/system/diagnostics").json()
    checks = {row["name"]: row for row in body["checks"]}

    assert checks["Node"]["required"] is False
    assert body["required_ok"] is True
    assert "Node" in body["optional_failures"]
    assert body["all_ok"] is True
