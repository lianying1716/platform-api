"""素材状态对账：把「卡在进行中」的素材拉回真实状态。

背景（实测发现的真 bug）：
``handle_download`` 在真正下载**之前**就把 ``download_status`` 置为 ``downloading`` 并提交，
这样前端能立刻看到进度。但如果 worker 进程崩溃、被 kill，或 handler 直接抛异常，
这行素材就永远停在 ``downloading``——队列里那条 job 已经 error/消失，却没有任何东西去复位它。
转录（transcribing）、分析（analyzing）同理。

对账规则（诚实优先，绝不谎报成功）：
对每个「进行中」状态的素材，若既没有对应的 queued/running 任务、且状态已超过阈值时间没更新，
说明这份工作不会再发生 → 复位为 ``error``，并写一条可读原因，让用户能显式重试。
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JobQueue, MediaAsset

# 进行中状态 → 负责推进它的 job_type
_IN_PROGRESS = {
    "download_status": ("downloading", "download"),
    "transcribe_status": ("transcribing", "transcribe"),
    "analyze_status": ("analyzing", "analyze"),
}

_STALE_MESSAGE = "任务中断（worker 异常或重启），可重新触发"

# 兜底阈值：超过这么久还没更新、且队列里也没有活任务，就判定为中断。
DEFAULT_STALE_MINUTES = 30


def _live_asset_ids(db: Session, job_type: str) -> set[int]:
    """当前 queued/running 任务覆盖的 asset_id 集合。"""
    rows = db.scalars(
        select(JobQueue).where(
            JobQueue.job_type == job_type,
            JobQueue.status.in_(("queued", "running")),
        )
    ).all()
    out: set[int] = set()
    for job in rows:
        raw = (job.payload or {}).get("asset_id")
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def clear_success_error(db: Session) -> int:
    """清掉「已成功」素材上残留的 error。

    ``media_asset.error`` 是共享错误位。历史上有别的阶段（视频理解等）把自己的失败
    写进这里，导致 download_status='done' 的素材在前端显示一条失败信息。
    这里把"下载成功后仍挂着 error"的行修正回干净状态。
    """
    rows = db.scalars(
        select(MediaAsset).where(
            MediaAsset.download_status == "done",
            MediaAsset.error != "",
        )
    ).all()
    for asset in rows:
        asset.error = ""
    if rows:
        db.commit()
    return len(rows)


def reset_stale_assets(
    db: Session, *, older_than_minutes: int = DEFAULT_STALE_MINUTES
) -> dict[str, int]:
    """把中断的进行中素材复位为 error。返回每个状态各修了几条。"""
    cutoff = datetime.utcnow() - timedelta(minutes=max(1, older_than_minutes))
    fixed: dict[str, int] = {}
    for column, (in_progress, job_type) in _IN_PROGRESS.items():
        live = _live_asset_ids(db, job_type)
        assets = db.scalars(
            select(MediaAsset).where(
                getattr(MediaAsset, column) == in_progress,
                MediaAsset.updated_at < cutoff,
            )
        ).all()
        count = 0
        for asset in assets:
            if asset.id in live:
                continue  # 队列里还有活任务，交给它推进
            setattr(asset, column, "error")
            if not (asset.error or "").strip():
                asset.error = _STALE_MESSAGE
            count += 1
        if count:
            fixed[column] = count
    if fixed:
        db.commit()
    return fixed
