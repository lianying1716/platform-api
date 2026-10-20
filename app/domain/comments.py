"""Comment collection policy shared by crawl and subscription flows."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.capabilities import COMMENT_MODES


@dataclass(frozen=True)
class CommentPolicy:
    mode: str = "gate"

    def __post_init__(self) -> None:
        if self.mode not in COMMENT_MODES:
            raise ValueError(f"评论模式必须是 {COMMENT_MODES} 之一")

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def force(self) -> bool:
        return self.mode == "force"


def comment_policy(mode: str | None) -> CommentPolicy:
    return CommentPolicy(mode or "gate")
