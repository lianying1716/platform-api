"""Declarative platform capabilities shared by API, services, and the UI.

This module is intentionally the single source of truth for what a platform can
actually do.  Transport schemas validate against it; the frontend reads the same
manifest from ``GET /api/platforms`` instead of maintaining parallel constants.
"""

from __future__ import annotations

from dataclasses import dataclass


SOURCE_LABELS: dict[str, str] = {
    "favorite": "收藏",
    "collect_folder": "收藏夹",
    "collection": "合集",
    "series": "短剧",
    "creator": "博主",
    "watch_later": "稍后再看",
}

QUALITY_LABELS: dict[str, str] = {
    "best": "最佳画质",
    "1080": "1080P",
    "720": "720P",
    "audio": "仅音频",
}

COMMENT_MODES = ("off", "gate", "force")
ALL_QUALITIES = ("best", "1080", "720", "audio")


@dataclass(frozen=True)
class PlatformCapability:
    platform: str
    label: str
    source_types: tuple[str, ...] = ()
    download_qualities: tuple[str, ...] = ("best",)
    keyword_crawl: bool = False
    comments: bool = False
    follow_import: bool = False

    def source_options(self) -> list[dict[str, str]]:
        return [
            {"value": value, "label": SOURCE_LABELS.get(value, value)}
            for value in self.source_types
        ]

    def quality_options(self) -> list[dict[str, str]]:
        return [
            {"value": value, "label": QUALITY_LABELS.get(value, value)}
            for value in self.download_qualities
        ]


# A source type is listed only when the adapter's ``fetch_source`` implements it.
# Listing a type that only appears in ``list_sources`` produces the old
# "created successfully but always zero items" failure.
CAPABILITIES: dict[str, PlatformCapability] = {
    "bilibili": PlatformCapability(
        platform="bilibili",
        label="B站",
        source_types=("favorite", "creator", "watch_later"),
        download_qualities=("best", "1080", "720", "audio"),
        keyword_crawl=True,
        comments=True,
        follow_import=True,
    ),
    "douyin": PlatformCapability(
        platform="douyin",
        label="抖音",
        source_types=("favorite", "collect_folder", "collection", "series", "creator"),
        download_qualities=("best", "1080", "720"),
        keyword_crawl=True,
        comments=True,
        follow_import=True,
    ),
    "xhs": PlatformCapability(
        platform="xhs",
        label="小红书",
        source_types=("creator",),
        download_qualities=("best", "1080", "720", "audio"),
        keyword_crawl=True,
        comments=True,
    ),
    "zhihu": PlatformCapability(
        platform="zhihu",
        label="知乎",
        source_types=("creator",),
        download_qualities=("best", "1080", "720", "audio"),
        keyword_crawl=True,
        comments=True,
    ),
    "kuaishou": PlatformCapability(
        platform="kuaishou",
        label="快手",
        download_qualities=("best", "1080", "720", "audio"),
        keyword_crawl=True,
        comments=True,
    ),
    "wechat_mp": PlatformCapability(
        platform="wechat_mp",
        label="微信公众号",
        download_qualities=(),
    ),
}

CRAWL_PLATFORMS = frozenset(p for p, cap in CAPABILITIES.items() if cap.keyword_crawl)


def capability_for(platform: str) -> PlatformCapability | None:
    return CAPABILITIES.get(platform)


def supported_source_types(platform: str) -> tuple[str, ...]:
    cap = capability_for(platform)
    return cap.source_types if cap else ()


def supported_qualities(platform: str) -> tuple[str, ...]:
    cap = capability_for(platform)
    return cap.download_qualities if cap else ()


def require_source_type(platform: str, source_type: str) -> None:
    cap = capability_for(platform)
    allowed = cap.source_types if cap else ()
    if source_type not in allowed:
        options = "、".join(SOURCE_LABELS.get(v, v) for v in allowed) or "无"
        raise ValueError(f"{cap.label if cap else platform} 不支持来源类型 {source_type!r}；可用类型：{options}")


def require_quality(platform: str, quality: str) -> None:
    allowed = supported_qualities(platform)
    if quality not in allowed:
        options = "、".join(QUALITY_LABELS.get(v, v) for v in allowed) or "无"
        raise ValueError(f"{platform} 不支持下载画质 {quality!r}；可用画质：{options}")


def as_public_manifest() -> list[dict]:
    """Shape consumed by ``GET /api/platforms`` and the frontend option lists."""
    return [
        {
            "platform": cap.platform,
            "label": cap.label,
            "source_types": cap.source_options(),
            "download_qualities": cap.quality_options(),
            "keyword_crawl": cap.keyword_crawl,
            "comments": cap.comments,
            "follow_import": cap.follow_import,
        }
        for cap in CAPABILITIES.values()
    ]


def manifest_by_platform() -> dict[str, dict]:
    return {row["platform"]: row for row in as_public_manifest()}
