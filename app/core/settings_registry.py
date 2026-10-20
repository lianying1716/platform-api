"""Typed registry for subscription-level settings that may inherit globals.

The registry intentionally stays small: it owns the contracts that previously
drifted between ORM defaults, Pydantic defaults, scheduler logic, and Vue forms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: Any
    nullable: bool
    consumer: str


SPECS: dict[str, SettingSpec] = {
    "sync_interval_minutes": SettingSpec(
        key="sync_interval_minutes",
        default=None,
        nullable=True,
        consumer="app.core.scheduler.enqueue_due_subscriptions",
    ),
    "process_auto_transcribe": SettingSpec(
        key="process_auto_transcribe",
        default=None,
        nullable=True,
        consumer="app.services.auto_process.auto_process_flags",
    ),
    "process_auto_analyze": SettingSpec(
        key="process_auto_analyze",
        default=None,
        nullable=True,
        consumer="app.services.auto_process.auto_process_flags",
    ),
}


def get_spec(key: str) -> SettingSpec:
    return SPECS[key]


def resolve_inherited(value: Any, default: Any) -> Any:
    """None means inherit; False is an explicit override and must remain False."""
    return default if value is None else value


def resolve_subscription_interval(subscription, settings_map: dict) -> int:
    """Resolve a subscription interval; random global range is sampled per scan."""
    from app.core.sync_settings import pick_interval_minutes

    if getattr(subscription, "interval_minutes", None) is not None:
        return max(10, int(subscription.interval_minutes))
    return max(10, int(pick_interval_minutes(settings_map)))
