"""Stable job outcome contract shared by every worker handler."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RetryClass(StrEnum):
    NEVER = "never"
    BACKOFF = "backoff"
    RISK_COOLDOWN = "risk_cooldown"
    MANUAL = "manual"


@dataclass(frozen=True)
class JobOutcome:
    ok: bool
    retry: RetryClass = RetryClass.NEVER
    code: str = ""
    message: str = ""

    def __post_init__(self) -> None:
        if self.ok and self.retry is not RetryClass.NEVER:
            raise ValueError("成功的任务不能携带重试策略")

    @property
    def retry_allowed(self) -> bool:
        return not self.ok and self.retry is not RetryClass.NEVER

    @classmethod
    def success(cls) -> "JobOutcome":
        return cls(ok=True)

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        retry: RetryClass = RetryClass.BACKOFF,
        code: str = "",
    ) -> "JobOutcome":
        return cls(ok=False, retry=retry, code=code, message=message)
