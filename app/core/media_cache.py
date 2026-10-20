"""Small disk cache for server-proxied remote images.

Keys are derived from a namespace plus immutable server-side identifiers.  The
cache never accepts a caller-provided filesystem path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.config import settings
from app.core.egress import EgressContentTypeBlocked, validate_image_bytes


def _base() -> Path:
    path = Path(settings.data_dir) / "media_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(namespace: str, *parts: object) -> str:
    raw = "|".join([namespace, *(str(part) for part in parts)]).encode()
    return hashlib.sha256(raw).hexdigest()


def read(namespace: str, *parts: object) -> tuple[str, bytes] | None:
    key = _key(namespace, *parts)
    meta = _base() / f"{key}.json"
    body = _base() / f"{key}.bin"
    try:
        data = json.loads(meta.read_text("utf-8"))
        payload = body.read_bytes()
        # The metadata file is local, but it may have been written by an older
        # build that trusted a remote Content-Type.  Revalidate on every read so
        # a pre-fix HTML/SVG cache entry cannot become same-origin content.
        content_type = validate_image_bytes(
            payload, str(data.get("type") or ""), allow_missing_type=True
        )
        return content_type, payload
    except (OSError, ValueError, EgressContentTypeBlocked):
        return None


def write(namespace: str, *parts: object, content_type: str, body: bytes) -> None:
    key = _key(namespace, *parts)
    try:
        (_base() / f"{key}.bin").write_bytes(body)
        (_base() / f"{key}.json").write_text(
            json.dumps({"type": content_type}), "utf-8"
        )
    except OSError:
        # A cache miss is always recoverable; it must not turn a valid image
        # response into an error.
        pass
