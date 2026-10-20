"""自动加工决策：内容下载完成后，是否自动转录 / 自动分析。

两级开关，订阅级优先，缺省回落到全局默认（`process_auto_transcribe` / `process_auto_analyze`）：

- 自动转录：登记 transcribe（basic 档）。
- 自动分析：登记 analyze；工作流引擎自动补齐并等待转录前置。

设计边界：这里只做「要不要加工」的决策，工作流引擎负责依赖与入队。
既有的热度门控视频理解路径（process_gate）保持独立判断，但同样只登记步骤。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ContentItem, MediaAsset, Subscription

def _subscription_for(db: Session, content: ContentItem) -> Subscription | None:
    if content.source_kind == "subscription" and content.source_id:
        return db.get(Subscription, content.source_id)
    return None


def _resolve_flag(sub: Subscription | None, attr: str, default: bool) -> bool:
    """Resolve a tri-state subscription override (None means inherit)."""
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
    """按自动加工开关登记转录 / 分析步骤。

    仅对**视频且下载完成**的素材生效（转录/分析要本地音视频）。工作流引擎
    会先建前置步骤，只有转录完成后 analyze 才会入队。
    """
    if content.content_type != "video" or asset.download_status != "done":
        return
    want_transcribe, want_analyze = auto_process_flags(db, content, settings)
    # 自动分析依赖转录文本：工作流补齐并等待转录，成功后自动放行 analyze。
    if not want_transcribe and not want_analyze:
        return
    from app.services.workflow_engine import register_workflow

    steps = ["transcribe"] if want_transcribe or want_analyze else []
    if want_analyze:
        steps.append("analyze")
    register_workflow(db, asset.id, steps, options={"force": False})


def maybe_chain_analyze(db: Session, asset: MediaAsset, content: ContentItem | None, settings: dict) -> None:
    """转录完成后：若该内容开了自动分析，登记 analyze 步骤。

    这是旧调用方的兼容入口。新路径由工作流在转录 job 成功终态自动放行，
    runner 不再依赖 handler 内部的隐式链式调用。
    """
    if content is None:
        return
    _, want_analyze = auto_process_flags(db, content, settings)
    if want_analyze:
        from app.services.workflow_engine import register_workflow

        register_workflow(db, asset.id, ["analyze"], options={"force": False})
