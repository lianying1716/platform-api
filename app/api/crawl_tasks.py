"""关键词采集任务 API（P5）。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.queue import enqueue
from app.db import get_db
from app.models import CrawlRun, CrawlTask
from app.modules import REGISTRY

router = APIRouter(prefix="/api/crawl-tasks", tags=["crawl"], dependencies=[Depends(security.require_admin)])

# MediaCrawler 支持的平台（一期）
_CRAWL_PLATFORMS = {"xhs", "douyin", "bilibili", "kuaishou", "zhihu"}


class CrawlTaskIn(BaseModel):
    platform: str
    keyword: str
    sort: str = "popular"
    limit_per_run: int = 50
    with_comments: bool = False
    to_candidate: bool = True
    min_play: int = 0
    interval_minutes: int = 1440
    enabled: bool = True


def _out(t: CrawlTask) -> dict:
    return {
        "id": t.id,
        "platform": t.platform,
        "keyword": t.keyword,
        "sort": t.sort,
        "limit_per_run": t.limit_per_run,
        "with_comments": t.with_comments,
        "to_candidate": t.to_candidate,
        "min_play": t.min_play,
        "interval_minutes": t.interval_minutes,
        "enabled": t.enabled,
        "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
    }


@router.get("")
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.scalars(select(CrawlTask).order_by(CrawlTask.id.desc())).all()
    return [_out(t) for t in tasks]


@router.post("")
def create_task(body: CrawlTaskIn, db: Session = Depends(get_db)):
    if body.platform not in _CRAWL_PLATFORMS:
        raise HTTPException(status_code=422, detail=f"不支持的采集平台: {body.platform}")
    if not body.keyword.strip():
        raise HTTPException(status_code=422, detail="关键词不能为空")
    task = CrawlTask(**body.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return _out(task)


@router.put("/{task_id}")
def update_task(task_id: int, body: CrawlTaskIn, db: Session = Depends(get_db)):
    task = db.get(CrawlTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    for k, v in body.model_dump().items():
        setattr(task, k, v)
    db.commit()
    return _out(task)


@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(CrawlTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    db.delete(task)
    db.commit()
    return {"ok": True}


@router.post("/{task_id}/run")
def run_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(CrawlTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    job = enqueue(db, "crawl", {"task_id": task_id})
    return {"job_id": job.id}


@router.get("/{task_id}/runs")
def list_runs(task_id: int, db: Session = Depends(get_db), limit: int = 20):
    runs = db.scalars(
        select(CrawlRun).where(CrawlRun.task_id == task_id).order_by(CrawlRun.id.desc()).limit(min(limit, 100))
    ).all()
    return [
        {
            "id": r.id,
            "status": r.status,
            "fetched": r.fetched,
            "added": r.added,
            "error": r.error,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in runs
    ]
