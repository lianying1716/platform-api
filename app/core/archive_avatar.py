"""Resolve legacy archive-avatar URLs to files below the configured data dir."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit


ARCHIVE_AVATAR_PREFIX = "/api/archive/avatar/"
ARCHIVE_AVATAR_DIR = "archive_avatars"


class ArchiveAvatarInvalid(ValueError):
    """The stored avatar value is not a safe single-file archive reference."""


def _safe_name(name: str) -> str:
    value = (name or "").strip()
    if not value or value in (".", ".."):
        raise ArchiveAvatarInvalid("头像文件名非法")
    if "\x00" in value or "/" in value or "\\" in value:
        raise ArchiveAvatarInvalid("头像文件名必须为单层文件名")
    if Path(value).name != value:
        raise ArchiveAvatarInvalid("头像文件名必须为单层文件名")
    return value


def archive_avatar_name(value: str) -> str | None:
    """Return the filename from an exact same-origin archive URL, else ``None``.

    Only the canonical ``/api/archive/avatar/<name>`` form is accepted.  A
    remote URL, query string, fragment or encoded path separator is rejected
    before the filesystem resolver sees it.
    """
    try:
        parsed = urlsplit((value or "").strip())
    except ValueError:
        # Let the remote-image path produce its normal 422 for malformed URLs
        # instead of turning a stored bad value into a 500.
        return None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    if not parsed.path.startswith(ARCHIVE_AVATAR_PREFIX):
        return None
    raw_name = parsed.path[len(ARCHIVE_AVATAR_PREFIX) :]
    try:
        name = unquote(raw_name, errors="strict")
    except UnicodeDecodeError as exc:
        raise ArchiveAvatarInvalid("头像文件名编码非法") from exc
    return _safe_name(name)


def resolve_archive_avatar(name: str, data_dir: Path | str) -> Path:
    """Resolve an archive avatar without allowing traversal or symlink escape."""
    safe = _safe_name(name)
    base = (Path(data_dir) / ARCHIVE_AVATAR_DIR).resolve()
    path = (base / safe).resolve(strict=False)
    if not path.is_relative_to(base):
        raise ArchiveAvatarInvalid("头像文件越出归档目录")
    return path
