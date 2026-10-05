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
from app.models import JobQueue

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
    return [
        {
            "id": j.id,
            "job_type": j.job_type,
            "status": j.status,
            "attempts": j.attempts,
            "max_attempts": j.max_attempts,
            "priority": j.priority,
            "error": j.error,
            "payload": j.payload,
            "run_after": j.run_after.isoformat() if j.run_after else None,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        }
        for j in jobs
    ]


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
