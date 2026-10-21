#!/usr/bin/env python3
"""Audit frontend pages that still bypass generated operation IDs.

The existing route test only proves a literal URL exists.  It cannot prove the
HTTP method, body shape, or response fields match OpenAPI.  This report groups
the remaining hand-written calls by risk so migration can proceed from the
highest-value surfaces rather than a blind rename.

Usage:
    python scripts/audit_frontend_api_calls.py [--json]
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "src"
CALL = re.compile(r"api\.(get|post|put|patch|delete)\(\s*(?:`([^`]+)`|['\"]([^'\"]+)['\"])")
METHOD_RISK = {"post": 3, "put": 3, "patch": 3, "delete": 3, "get": 1}


@dataclass(frozen=True)
class CallSite:
    file: str
    line: int
    method: str
    path: str
    risk: int


def audit() -> list[CallSite]:
    rows: list[CallSite] = []
    for source in sorted(WEB.rglob("*")):
        if source.suffix not in {".ts", ".vue"} or source.name == "api.generated.ts":
            continue
        text = source.read_text(encoding="utf-8")
        for match in CALL.finditer(text):
            method = match.group(1).lower()
            path = match.group(2) or match.group(3) or ""
            rows.append(
                CallSite(
                    file=str(source.relative_to(ROOT)),
                    line=text.count("\n", 0, match.start()) + 1,
                    method=method.upper(),
                    path=path,
                    risk=METHOD_RISK[method],
                )
            )
    return rows


def summarize(rows: list[CallSite]) -> dict:
    by_file: dict[str, int] = {}
    for row in rows:
        by_file[row.file] = by_file.get(row.file, 0) + 1
    return {
        "total": len(rows),
        "write_calls": sum(1 for row in rows if row.risk >= 3),
        "read_calls": sum(1 for row in rows if row.risk < 3),
        "by_file": dict(sorted(by_file.items(), key=lambda item: (-item[1], item[0]))),
        "highest_risk": [asdict(row) for row in sorted(rows, key=lambda row: (-row.risk, row.file, row.line))[:40]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = summarize(audit())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"hand-written calls={report['total']} writes={report['write_calls']} reads={report['read_calls']}")
        for path, count in report["by_file"].items():
            print(f"{count:3} {path}")
        print("\nHighest-risk call sites:")
        for row in report["highest_risk"]:
            print(f"{row['method']:6} {row['file']}:{row['line']} {row['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
