"""Validated download policy used at the download boundary."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.capabilities import require_quality


@dataclass(frozen=True)
class DownloadPolicy:
    quality: str = "best"

    def __post_init__(self) -> None:
        if self.quality not in ("best", "1080", "720", "audio"):
            raise ValueError(f"未知下载画质: {self.quality!r}")

    @property
    def audio_only(self) -> bool:
        return self.quality == "audio"

    @property
    def max_height(self) -> int | None:
        if self.quality == "1080":
            return 1080
        if self.quality == "720":
            return 720
        return None

    def validate_for(self, platform: str) -> None:
        require_quality(platform, self.quality)


def policy_for(platform: str, quality: str | None) -> DownloadPolicy:
    policy = DownloadPolicy(quality or "best")
    policy.validate_for(platform)
    return policy
