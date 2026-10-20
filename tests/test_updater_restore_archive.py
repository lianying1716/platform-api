"""OPS-02: backup archive contract and pg_restore invocation."""

import io
import json
import tarfile
from pathlib import Path

from app.updater import service as updater_service


def _archive(path: Path, *, manifest=None, dump=b"PGDMP"):
    with tarfile.open(path, "w:gz") as tar:
        data = json.dumps(manifest or {"includes": ["db.dump", "data.tar.gz"]}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("db.dump")
        info.size = len(dump)
        tar.addfile(info, io.BytesIO(dump))


def test_restore_feeds_dump_to_pg_restore(tmp_path, monkeypatch):
    pkg = tmp_path / "backup.tar.gz"
    _archive(pkg)
    seen = {}

    class Done:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["input"] = kwargs.get("input")
        return Done()

    monkeypatch.setattr(updater_service.subprocess, "run", fake_run)
    result = updater_service._do_restore(str(pkg))
    assert result["ok"] is True
    assert seen["input"] == b"PGDMP"
    assert "pg_restore" in seen["args"]
    assert "--clean" in seen["args"] and "--if-exists" in seen["args"]


def test_restore_rejects_archive_without_db_dump(tmp_path, monkeypatch):
    pkg = tmp_path / "bad.tar.gz"
    with tarfile.open(pkg, "w:gz") as tar:
        payload = json.dumps({"includes": []}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    called = []
    monkeypatch.setattr(updater_service.subprocess, "run", lambda *a, **k: called.append(a))
    result = updater_service._do_restore(str(pkg))
    assert result["ok"] is False
    assert called == []


def test_restore_reports_pg_restore_failure(tmp_path, monkeypatch):
    pkg = tmp_path / "backup.tar.gz"
    _archive(pkg)

    class Failed:
        returncode = 1
        stderr = b"role does not exist"

    monkeypatch.setattr(updater_service.subprocess, "run", lambda *a, **k: Failed())
    result = updater_service._do_restore(str(pkg))
    assert result["ok"] is False
    assert "pg_restore" in result["error"]
