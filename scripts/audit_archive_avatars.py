#!/usr/bin/env python3
"""Read-only classification of stored creator avatars for UI-14.

The production incident was not only a proxy bug: historical rows contain a
mixture of canonical archive paths, missing files and unrelated values.  This
script reports those buckets without modifying the database or filesystem so an
operator can decide what to repair before re-running the UI acceptance.

Usage:
    python scripts/audit_archive_avatars.py
    python scripts/audit_archive_avatars.py --json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.core.archive_avatar import (
    ArchiveAvatarInvalid,
    archive_avatar_name,
    resolve_archive_avatar,
)
from app.db import SessionLocal
from app.models import CreatorProfile


@dataclass(frozen=True)
class AvatarBucket:
    name: str
    count: int
    examples: list[str]


def classify(profiles, data_dir: Path) -> dict:
    """Classify rows into stable categories without touching live data."""
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    remote = 0
    empty = 0
    for profile in profiles:
        value = str(profile.avatar or "").strip()
        if not value:
            empty += 1
            continue
        try:
            name = archive_avatar_name(value)
        except ArchiveAvatarInvalid:
            bucket = "invalid_archive_reference"
        else:
            if name is None:
                if value.startswith(("http://", "https://")):
                    remote += 1
                    continue
                bucket = "unsupported_reference"
            else:
                try:
                    path = resolve_archive_avatar(name, data_dir)
                except ArchiveAvatarInvalid:
                    bucket = "invalid_archive_reference"
                else:
                    bucket = "restorable_archive_file" if path.is_file() else "missing_archive_file"
        label = f"profile={profile.id} {value}" if profile.id is not None else value
        counts[bucket] += 1
        if len(examples.setdefault(bucket, [])) < 5:
            examples[bucket].append(label)

    keys = (
        "restorable_archive_file",
        "missing_archive_file",
        "invalid_archive_reference",
        "unsupported_reference",
    )
    buckets = [AvatarBucket(name=key, count=counts[key], examples=examples.get(key, [])) for key in keys]
    return {
        "total_profiles": len(profiles),
        "empty": empty,
        "remote_http": remote,
        "archive_total": sum(item.count for item in buckets[:3]),
        "buckets": [asdict(item) for item in buckets],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        profiles = db.scalars(select(CreatorProfile).order_by(CreatorProfile.id)).all()
    finally:
        db.close()
    report = classify(profiles, Path(settings.data_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"profiles={report['total_profiles']} empty={report['empty']} remote_http={report['remote_http']}")
        for bucket in report["buckets"]:
            print(f"{bucket['name']}={bucket['count']}")
            for example in bucket["examples"]:
                print(f"  {example}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
