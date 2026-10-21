"""UI-14: classify legacy avatars without mutating production data."""

from pathlib import Path
from types import SimpleNamespace

from scripts.audit_archive_avatars import classify


def _profile(profile_id: int, avatar: str):
    return SimpleNamespace(id=profile_id, avatar=avatar)


def test_avatar_audit_separates_restorable_missing_and_invalid(tmp_path: Path):
    archive = tmp_path / "archive_avatars"
    archive.mkdir()
    # Minimal JPEG signature is enough for this filesystem-only audit.
    (archive / "ok.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"jpeg")
    rows = [
        _profile(1, "/api/archive/avatar/ok.jpg"),
        _profile(2, "/api/archive/avatar/missing.jpg"),
        _profile(3, "/api/archive/avatar/../secret.jpg"),
        _profile(4, "https://example.com/avatar.jpg"),
        _profile(5, "relative/avatar.jpg"),
        _profile(6, ""),
    ]
    report = classify(rows, tmp_path)
    buckets = {row["name"]: row for row in report["buckets"]}

    assert buckets["restorable_archive_file"]["count"] == 1
    assert buckets["missing_archive_file"]["count"] == 1
    assert buckets["invalid_archive_reference"]["count"] == 1
    assert buckets["unsupported_reference"]["count"] == 1
    assert report["remote_http"] == 1
    assert report["empty"] == 1


def test_avatar_audit_examples_are_bounded(tmp_path: Path):
    rows = [_profile(i, f"/api/archive/avatar/missing-{i}.jpg") for i in range(20)]
    report = classify(rows, tmp_path)
    missing = next(row for row in report["buckets"] if row["name"] == "missing_archive_file")
    assert missing["count"] == 20
    assert len(missing["examples"]) == 5
