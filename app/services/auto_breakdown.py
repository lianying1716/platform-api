"""自动爆款拆解：下载完成后按设置页配置的「阀门」判定是不是爆款，命中则自动加工。

判定用的是 `breakdown_service._data_context`（确定性数字：本条播放/点赞/互动率 +
相对作者中位数倍数），阀门与命中规则见 `core/breakdown_settings.is_viral`。

阀门设计目标（给用户一眼能懂、又能直接调）：
- 相对自身中位数倍数 —— "比自己平时火"，最贴近"爆款"直觉（默认 3 倍，始终参与）；
- 播放 / 点赞绝对门槛 —— "体量够大"，默认 0=不限；
- 互动率门槛 —— "观众真参与了"，默认 0=不限；
- 命中规则 any/all —— 任一命中 / 全部满足，默认 any。

命中后把开关映射为工作流步骤：自动转录 / 自动视频理解 / 自动爆款拆解。拆解声明的
前置是转录+理解，工作流引擎会自动补齐并等待，不会让三步抢跑。

边界：只对**视频且下载完成**的素材生效；这里只做判定 + 注册步骤，真正执行由 worker 负责。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.breakdown_settings import get_breakdown_settings, is_viral
from app.models import ContentItem, MediaAsset

logger = logging.getLogger("platform.auto_breakdown")

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


def enqueue_auto_breakdown(
    db: Session,
    asset: MediaAsset,
    content: ContentItem,
    *,
    settings: dict | None = None,
) -> bool:
    """命中阀门则注册自动加工工作流；返回是否命中。不 commit。

    下载完成只登记步骤。转录/理解/拆解是否真正入队，由 ``workflow_engine``
    按依赖放行；拆解不会在转录和视频理解完成前调用模型。
    """
    if content.content_type != "video" or asset.download_status != "done":
        return False
    hit, cfg, ctx = _evaluate(db, content, asset)
    if not hit:
        return False
    from app.core.process_settings import get_process_settings
    from app.services.workflow_engine import register_workflow

    proc = settings if settings is not None else get_process_settings(db)
    from app.services.auto_process import auto_process_flags

    want_transcribe, want_analyze = auto_process_flags(db, content, proc)
    want_understand = bool(cfg.get("auto_understand"))
    want_breakdown = bool(cfg.get("auto_breakdown"))
    steps: list[str] = []
    if want_transcribe or want_analyze:
        steps.append("transcribe")
    if want_analyze:
        steps.append("analyze")
    if want_understand or want_breakdown:
        steps.append("understand_video")
    if want_breakdown:
        steps.append("breakdown")
    if steps:
        register_workflow(db, asset.id, steps, options={"force": False})
        db.commit()

    logger.info(
        "自动爆款命中 asset=%s play=%s like=%s rate=%.4f (understand=%s breakdown=%s)",
        asset.id, ctx.get("play"), ctx.get("like"), float(ctx.get("engagement_rate") or 0),
        want_understand, want_breakdown,
    )
    return True
