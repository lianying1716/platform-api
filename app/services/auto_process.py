"""自动加工决策：内容下载完成后，是否自动转录 / 自动分析。

两级开关，订阅级优先，缺省回落到全局默认（`process_auto_transcribe` / `process_auto_analyze`）：

- 自动转录：直接入队 transcribe（basic 档）。
- 自动分析：直接入队 analyze；analyze 依赖转录文本，若尚未转录则先补一次转录，
  转录完成后再由 handle_transcribe 链式补 analyze。

设计边界：这里只做「要不要入队」的决策 + 入队，真正加工由 worker 负责。
既有的热度门控视频理解路径（process_gate）保持不变，两者靠 queue 去重合并。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.queue import enqueue
from app.models import ContentItem, MediaAsset, Subscription

# 与 process_gate 一致：加工优先级低于核心管线（download/transcribe/analyze=100）。
_PROCESS_PRIORITY = 120

# 正在跑或已完成的状态：不应重复入队。注意 "pending" 是 MediaAsset 的初始态
# （尚未加工），不是"在途"，所以这里不含 pending；重复入队由 queue 去重吸收。
_TRANSCRIBE_BUSY = ("done", "doing", "transcribing")
_ANALYZE_BUSY = ("done", "doing", "analyzing")


def _subscription_for(db: Session, content: ContentItem) -> Subscription | None:
    if content.source_kind == "subscription" and content.source_id:
        return db.get(Subscription, content.source_id)
    return None


def _resolve_flag(sub: Subscription | None, attr: str, default: bool) -> bool:
    """订阅级开关优先；订阅不存在时用全局默认。"""
    if sub is None:
        return default
    value = getattr(sub, attr, None)
    if value is None:
        return default
    return bool(value)


def auto_process_flags(db: Session, content: ContentItem, settings: dict) -> tuple[bool, bool]:
    """返回 (是否自动转录, 是否自动分析)。订阅级开关覆盖全局默认。"""
    sub = _subscription_for(db, content)
    want_transcribe = _resolve_flag(sub, "auto_transcribe", bool(settings.get("auto_transcribe")))
    want_analyze = _resolve_flag(sub, "auto_analyze", bool(settings.get("auto_analyze")))
    return want_transcribe, want_analyze


def enqueue_auto_process(db: Session, asset: MediaAsset, content: ContentItem, settings: dict) -> None:
    """按自动加工开关给素材入队转录 / 分析任务。

    仅对**视频且下载完成**的素材生效（转录/分析要本地音视频）。不 commit：
    调用方（download handler）在同一次提交里落库。重复入队由 queue 去重吸收。
    """
    if content.content_type != "video" or asset.download_status != "done":
        return
    want_transcribe, want_analyze = auto_process_flags(db, content, settings)
    # 自动分析依赖转录文本：只要开了自动分析且尚未转录，就把转录作为前置一并入队，
    # 转录完成后由 handle_transcribe 链式补 analyze。
    need_transcribe = want_transcribe or (
        want_analyze and asset.transcribe_status != "done"
    )
    _enqueue_transcribe_if_needed(db, asset, force=need_transcribe)
    if want_analyze and asset.transcribe_status == "done":
        _enqueue_analyze(db, asset)


def _enqueue_transcribe_if_needed(db: Session, asset: MediaAsset, *, force: bool) -> None:
    if not force:
        return
    if asset.transcribe_status in _TRANSCRIBE_BUSY:
        return
    asset.transcribe_status = "pending"
    enqueue(db, "transcribe", {"asset_id": asset.id}, priority=_PROCESS_PRIORITY, commit=False)


def _enqueue_analyze(db: Session, asset: MediaAsset) -> None:
    if asset.analyze_status in _ANALYZE_BUSY:
        return
    asset.analyze_status = "pending"
    enqueue(db, "analyze", {"asset_id": asset.id}, priority=_PROCESS_PRIORITY, commit=False)


def maybe_chain_analyze(db: Session, asset: MediaAsset, content: ContentItem | None, settings: dict) -> None:
    """转录完成后：若该内容开了自动分析，链式补一次 analyze。"""
    if content is None:
        return
    _, want_analyze = auto_process_flags(db, content, settings)
    if want_analyze:
        _enqueue_analyze(db, asset)
