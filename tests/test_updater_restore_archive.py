"""OPS-02/OPS-16: backup archive contract, pg_restore and data volume restore."""

import io
import json
import tarfile
import gzip
from pathlib import Path

from app.updater import service as updater_service


def _archive(path: Path, *, manifest=None, dump=b"PGDMP", data=b"DATA"):
    with tarfile.open(path, "w:gz") as tar:
        manifest_bytes = json.dumps(manifest or {"includes": ["db.dump", "data.tar.gz"]}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        info = tarfile.TarInfo("db.dump")
        info.size = len(dump)
        tar.addfile(info, io.BytesIO(dump))
        # A real data.tar.gz contains at least one file; gzip of an empty tar
        # would be structurally valid but provide nothing to restore.
        inner = io.BytesIO()
        with tarfile.open(fileobj=inner, mode="w:gz") as nested:
            info = tarfile.TarInfo("marker.txt")
            info.size = len(data)
            nested.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("data.tar.gz")
        info.size = len(inner.getvalue())
        tar.addfile(info, io.BytesIO(inner.getvalue()))


def test_restore_feeds_dump_to_pg_restore(tmp_path, monkeypatch):
    pkg = tmp_path / "backup.tar.gz"
    _archive(pkg)
    seen = {}

    class Done:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        if "pg_restore" in args:
            seen["args"] = args
            stream = kwargs.get("stdin")
            assert stream is not None, "pg_restore payload must be streamed from a file"
            seen["input"] = stream.read()
        return Done()

    monkeypatch.setattr(updater_service.subprocess, "run", fake_run)
    monkeypatch.setattr(updater_service, "_data_volume_name", lambda: "test_platform-data")
    result = updater_service._do_restore(str(pkg))
    assert result["ok"] is True
    assert result["db_restored"] is True and result["data_restored"] is True
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
    assert result["db_restored"] is False and result["data_restored"] is False
    assert "pg_restore" in result["error"]


def test_restore_rejects_archive_without_data_tarball_before_touching_db(tmp_path, monkeypatch):
    pkg = tmp_path / "db-only.tar.gz"
    with tarfile.open(pkg, "w:gz") as tar:
        manifest = json.dumps({"includes": ["db.dump", "data.tar.gz"]}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
        info = tarfile.TarInfo("db.dump")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"PGDMP"))
    called = []
    monkeypatch.setattr(updater_service.subprocess, "run", lambda *a, **k: called.append(a))
    result = updater_service._do_restore(str(pkg))
    assert result["ok"] is False
    assert "data.tar.gz" in result["error"]
    assert called == [], "a split backup must be rejected before pg_restore"


def test_data_restore_failure_keeps_database_restore_fact(tmp_path, monkeypatch):
    pkg = tmp_path / "backup.tar.gz"
    _archive(pkg)

    class Done:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        if "docker" in args and "run" in args:
            class Failed:
                returncode = 1
                stderr = b"no space left on device"
            return Failed()
        return Done()

    monkeypatch.setattr(updater_service.subprocess, "run", fake_run)
    monkeypatch.setattr(updater_service, "_data_volume_name", lambda: "test_platform-data")
    result = updater_service._do_restore(str(pkg))
    assert result["ok"] is False
    assert result["db_restored"] is True
    assert result["data_restored"] is False
    assert "no space left" in result["error"]


def test_restore_accepts_dot_prefixed_members_from_real_backup(tmp_path, monkeypatch):
    """scripts/backup.sh and _do_backup use ``tar -C work .`` -> ``./name``."""
    pkg = tmp_path / "dot-prefixed.tar.gz"
    with tarfile.open(pkg, "w:gz") as tar:
        manifest = json.dumps({"includes": ["db.dump", "data.tar.gz"]}).encode()
        for name, payload in (
            ("./manifest.json", manifest),
            ("./db.dump", b"PGDMP"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        inner = io.BytesIO()
        with tarfile.open(fileobj=inner, mode="w:gz") as nested:
            info = tarfile.TarInfo("./secret.key")
            info.size = 3
            nested.addfile(info, io.BytesIO(b"key"))
        data = inner.getvalue()
        info = tarfile.TarInfo("./data.tar.gz")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    seen = {}

    class Done:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        if "pg_restore" in args:
            seen["dump"] = kwargs["stdin"].read()
        return Done()

    monkeypatch.setattr(updater_service.subprocess, "run", fake_run)
    monkeypatch.setattr(updater_service, "_data_volume_name", lambda: "test_platform-data")
    result = updater_service._do_restore(str(pkg))
    assert result["ok"] is True, result
    assert seen["dump"] == b"PGDMP"


def test_restore_rejects_unsafe_data_member_before_touching_db(tmp_path, monkeypatch):
    pkg = tmp_path / "unsafe.tar.gz"
    with tarfile.open(pkg, "w:gz") as tar:
        manifest = json.dumps({"includes": ["db.dump", "data.tar.gz"]}).encode()
        for name, payload in (("manifest.json", manifest), ("db.dump", b"PGDMP")):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        inner = io.BytesIO()
        with tarfile.open(fileobj=inner, mode="w:gz") as nested:
            info = tarfile.TarInfo("../outside.txt")
            info.size = 1
            nested.addfile(info, io.BytesIO(b"x"))
        data = inner.getvalue()
        info = tarfile.TarInfo("data.tar.gz")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    called = []
    monkeypatch.setattr(updater_service.subprocess, "run", lambda *a, **k: called.append(a))
    result = updater_service._do_restore(str(pkg))
    assert result["ok"] is False
    assert result["db_restored"] is False
    assert "越界" in result["error"]
    assert called == [], "unsafe data archive must be rejected before pg_restore"
