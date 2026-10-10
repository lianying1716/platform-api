#!/usr/bin/env python3
"""Verify that no Git commit still tracks vendored MediaCrawler assets.

Run from the platform-api repository root:

    python scripts/verify_history_clean.py

The check is intentionally textual and path-based: it must keep failing until
history is rewritten, not merely until HEAD deletes the files.
"""

from __future__ import annotations

import subprocess
import sys

FORBIDDEN_SUFFIXES = (
    "app/modules/crawler/libs/douyin.js",
    "app/modules/crawler/libs/stealth.min.js",
    "app/modules/crawler/libs/zhihu.js",
    "app/modules/crawler/graphql/kuaishou/search_query.graphql",
    "app/modules/crawler/graphql/kuaishou/vision_profile_user_list.graphql",
    "app/modules/crawler/douyin_sign.py",
    "app/modules/crawler/zhihu_sign.py",
)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> int:
    commits = _git("rev-list", "--all").splitlines()
    offenders: dict[str, list[str]] = {}
    for commit in commits:
        tree = _git("ls-tree", "-r", "--name-only", commit).splitlines()
        hits = [path for path in tree if path.endswith(FORBIDDEN_SUFFIXES)]
        if hits:
            offenders[commit] = hits

    if offenders:
        print(f"FAIL: {len(offenders)} commit(s) still contain vendored files", file=sys.stderr)
        for commit, paths in list(offenders.items())[:20]:
            summary = _git("show", "-s", "--format=%h %s", commit)
            print(f"  {summary}", file=sys.stderr)
            for path in paths:
                print(f"    - {path}", file=sys.stderr)
        return 1

    print(f"OK: {len(commits)} reachable commits contain no vendored MediaCrawler files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
