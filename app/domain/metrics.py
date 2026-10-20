"""Platform-neutral engagement metric normalization.

Missing data and a measured zero are different facts.  Every adapter and
worker uses this boundary so the API never presents unknown values as zero.
"""

from __future__ import annotations

from typing import Mapping


METRIC_FIELDS = ("play", "like", "comment", "favorite", "share")


def optional_int(value: object) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_metrics(raw: Mapping[str, object] | None) -> dict[str, int | None]:
    """Normalize a metrics mapping; absent/unparseable fields become ``None``."""
    source = raw if isinstance(raw, Mapping) else {}
    return {field: optional_int(source[field]) if field in source else None for field in METRIC_FIELDS}


def known_values(snapshots: list[object], field: str) -> list[float]:
    """Use only measured values in aggregations such as medians."""
    out: list[float] = []
    for row in snapshots:
        value = getattr(row, field, None)
        if value is not None:
            out.append(float(value))
    return out
