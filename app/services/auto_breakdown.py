"""自动爆款拆解：下载完成后按设置页配置的「阀门」判定是不是爆款，命中则自动加工。

判定用的是 `breakdown_service._data_context`（确定性数字：本条播放/点赞/互动率 +
相对作者中位数倍数），阀门与命中规则见 `core/breakdown_settings.is_viral`。

阀门设计目标（给用户一眼能懂、又能直接调）：
- 相对自身中位数倍数 —— "比自己平时火"，最贴近"爆款"直觉（默认 3 倍，始终参与）；
- 播放 / 点赞绝对门槛 —— "体量够大"，默认 0=不限；
- 互动率门槛 —— "观众真参与了"，默认 0=不限；
- 命中规则 any/all —— 任一命中 / 全部满足，默认 any。

命中后按三个开关分别入队：自动转录 / 自动视频理解 / 自动爆款拆解（后者依赖转录+理解，
所以会按需把前置任务一并入队，由既有链式逻辑补齐）。

边界：只对**视频且下载完成**的素材生效；这里只做判定 + 入队，真正加工由 worker 负责。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.breakdown_settings import get_breakdown_settings, is_viral
from app.core.queue import enqueue
from app.models import ContentItem, MediaAsset

logger = logging.getLogger("platform.auto_breakdown")

# 与 process_gate / auto_process 一致：加工优先级低于核心管线。
_PROCESS_PRIORITY = 120


def _data_context(db: Session, content: ContentItem, asset: MediaAsset) -> dict:
    """复用拆解服务里那套确定性数字（避免两处口径漂移）。"""
    from app.services.breakdown_service import _data_context as build

    return build(db, content, asset)


def _evaluate(db: Session, content: ContentItem, asset: MediaAsset) -> tuple[bool, dict, dict]:
    """返回 (是否爆款, 配置, 确定性数字)。开关全关 / 取数失败 → (False, ...)。"""
    cfg = get_breakdown_settings(db)
    if not (cfg.get("auto_breakdown") or cfg.get("auto_understand") or cfg.get("auto_transcribe")):
        return False, cfg, {}
    try:
        ctx = _data_context(db, content, asset)
    except Exception:  # noqa: BLE001 取数失败当不命中，不影响下载成功状态
        logger.exception("自动爆款判定取数失败，按非爆款处理 asset=%s", asset.id)
        return False, cfg, {}
    return is_viral(ctx, cfg), cfg, ctx


def should_auto_breakdown(db: Session, content: ContentItem, asset: MediaAsset) -> bool:
    """只判定不产生副作用（供测试 / 只读场景用）。"""
    if content.content_type != "video" or asset.download_status != "done":
        return False
    hit, _cfg, _ctx = _evaluate(db, content, asset)
    return hit


def enqueue_auto_breakdown(db: Session, asset: MediaAsset, content: ContentItem) -> bool:
    """命中阀门则按开关入队自动加工；返回是否命中并入了队。不 commit，调用方统一提交。"""
    if content.content_type != "video" or asset.download_status != "done":
        return False
    hit, cfg, ctx = _evaluate(db, content, asset)
    if not hit:
        return False
    want_understand = bool(cfg.get("auto_understand"))
    want_breakdown = bool(cfg.get("auto_breakdown"))

    want_transcribe = bool(cfg.get("auto_transcribe")) or want_understand or want_breakdown

    # 转录是理解 / 拆解的前置：需要就一并入队（去重由 queue 吸收）。
    if want_transcribe and asset.transcribe_status not in ("done", "doing", "transcribing"):
        asset.transcribe_status = "pending"
        enqueue(db, "transcribe", {"asset_id": asset.id}, priority=_PROCESS_PRIORITY, commit=False)

    if want_understand and asset.understand_status not in ("done", "doing", "understanding"):
        asset.understand_status = "pending"
        enqueue(db, "understand_video", {"asset_id": asset.id}, priority=_PROCESS_PRIORITY, commit=False)

    if want_breakdown:
        enqueue(db, "breakdown", {"asset_id": asset.id, "force": False}, priority=_PROCESS_PRIORITY, commit=False)

    logger.info(
        "自动爆款命中 asset=%s play=%s like=%s rate=%.4f (understand=%s breakdown=%s)",
        asset.id, ctx.get("play"), ctx.get("like"), float(ctx.get("engagement_rate") or 0),
        want_understand, want_breakdown,
    )
    return True
