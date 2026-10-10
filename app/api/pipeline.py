"""处理队列 API（P3）。

- 队列视图：按状态/类型筛选任务。
- 重试失败任务。
- 队列概览：各状态计数（前端流水线视图用）。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import queue, security
from app.db import get_db
from app.models import ContentItem, JobQueue, MediaAsset

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"], dependencies=[Depends(security.require_admin)])


@router.get("/jobs")
def list_jobs(
    db: Session = Depends(get_db),
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    stmt = select(JobQueue)
    if status:
        stmt = stmt.where(JobQueue.status == status)
    if job_type:
        stmt = stmt.where(JobQueue.job_type == job_type)
    stmt = stmt.order_by(JobQueue.id.desc()).limit(min(limit, 300)).offset(offset)
    jobs = db.scalars(stmt).all()

    # 关联出「这个任务在加工哪条素材」，让队列页能用人话展示在做什么，
    # 而不是只看到一个孤零零的 id。（payload 里存的是 asset_id / content_id / task_id）
    asset_ids = {int(j.payload.get("asset_id")) for j in jobs if (j.payload or {}).get("asset_id")}
    content_ids = {int(j.payload.get("content_id")) for j in jobs if (j.payload or {}).get("content_id")}
    asset_titles: dict[int, str] = {}
    content_titles: dict[int, str] = {}
    if asset_ids:
        rows = db.execute(
            select(MediaAsset.id, ContentItem.title)
            .join(ContentItem, MediaAsset.content_id == ContentItem.id)
            .where(MediaAsset.id.in_(asset_ids))
        ).all()
        asset_titles = {aid: (title or "") for aid, title in rows}
    if content_ids:
        rows = db.execute(
            select(ContentItem.id, ContentItem.title).where(ContentItem.id.in_(content_ids))
        ).all()
        content_titles = {cid: (title or "") for cid, title in rows}

    out = []
    for j in jobs:
        payload = j.payload or {}
        title = ""
        if payload.get("asset_id") is not None:
            title = asset_titles.get(int(payload["asset_id"]), "")
        elif payload.get("content_id") is not None:
            title = content_titles.get(int(payload["content_id"]), "")
        out.append(
            {
                "id": j.id,
                "job_type": j.job_type,
                "status": j.status,
                "attempts": j.attempts,
                "max_attempts": j.max_attempts,
                "priority": j.priority,
                "error": j.error,
                "payload": payload,
                # 供队列页展示「在加工什么」
                "target_title": title,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "locked_at": j.locked_at.isoformat() if j.locked_at else None,
                "run_after": j.run_after.isoformat() if j.run_after else None,
                "updated_at": j.updated_at.isoformat() if j.updated_at else None,
            }
        )
    return out


@router.get("/summary")
def queue_summary(db: Session = Depends(get_db)):
    """各 job_type x status 的计数，前端流水线视图用。"""
    rows = db.execute(
        select(JobQueue.job_type, JobQueue.status, func.count()).group_by(JobQueue.job_type, JobQueue.status)
    ).all()
    summary: dict[str, dict[str, int]] = {}
    for job_type, status, count in rows:
        summary.setdefault(job_type, {})[status] = count
    return summary


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db)):
    job = queue.retry(db, job_id)
    if job is None:
        raise HTTPException(status_code=400, detail="任务不存在或不是失败状态，无法重试")
    return {"id": job.id, "status": job.status}


@router.post("/jobs/retry-failed")
def retry_all_failed(db: Session = Depends(get_db), job_type: str | None = None):
    """批量重试所有失败任务。"""
    stmt = select(JobQueue).where(JobQueue.status == "error")
    if job_type:
        stmt = stmt.where(JobQueue.job_type == job_type)
    count = 0
    for job in db.scalars(stmt).all():
        if queue.retry(db, job.id):
            count += 1
    return {"retried": count}
