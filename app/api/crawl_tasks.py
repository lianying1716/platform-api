"""关键词采集任务 API（P5）。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.core import security
from app.application.commands.crawl import CrawlCommandError, enqueue_crawl
from app.application.commands.crawl_task import (
    CrawlTaskCommand,
    apply_crawl_task_command,
    create_crawl_task,
)
from app.db import get_db
from app.models import CrawlRun, CrawlTask

router = APIRouter(prefix="/api/crawl-tasks", tags=["crawl"], dependencies=[Depends(security.require_admin)])


class CrawlTaskIn(CrawlTaskCommand):
    @model_validator(mode="before")
    @classmethod
    def _translate_legacy_comment_bool(cls, data):
        if isinstance(data, dict) and "with_comments" in data and "comment_mode" not in data:
            data = dict(data)
            data["comment_mode"] = "force" if data.pop("with_comments") else "off"
        return data


def _out(t: CrawlTask) -> dict:
    return {
        "id": t.id,
        "platform": t.platform,
        "account_id": t.account_id,
        "keyword": t.keyword,
        "sort": t.sort,
        "limit_per_run": t.limit_per_run,
        "comment_mode": t.comment_mode,
        "to_candidate": t.to_candidate,
        "min_play": t.min_play,
        "interval_minutes": t.interval_minutes,
        "enabled": t.enabled,
        "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
        "last_attempt_at": t.last_attempt_at.isoformat() if t.last_attempt_at else None,
    }


@router.get("", response_model=list[schemas.CrawlTaskOut])
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.scalars(select(CrawlTask).order_by(CrawlTask.id.desc())).all()
    return [_out(t) for t in tasks]


@router.post("", response_model=schemas.CrawlTaskOut)
def create_task(body: CrawlTaskIn, db: Session = Depends(get_db)):
    try:
        task = create_crawl_task(db, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _out(task)


@router.put("/{task_id}", response_model=schemas.CrawlTaskOut)
def update_task(task_id: int, body: CrawlTaskIn, db: Session = Depends(get_db)):
    task = db.get(CrawlTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        task = apply_crawl_task_command(db, task, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _out(task)


@router.delete("/{task_id}", response_model=schemas.OkOut)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(CrawlTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    db.delete(task)
    db.commit()
    return {"ok": True}


@router.post("/{task_id}/run", response_model=schemas.JobRefOut)
def run_task(task_id: int, db: Session = Depends(get_db)):
    try:
        job = enqueue_crawl(db, task_id)
    except CrawlCommandError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job.id, "status": "queued"}


@router.get("/{task_id}/runs", response_model=list[schemas.CrawlRunOut])
def list_runs(task_id: int, db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=100)):
    runs = db.scalars(
        select(CrawlRun).where(CrawlRun.task_id == task_id).order_by(CrawlRun.id.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "job_id": r.job_id,
            "status": r.status,
            "account_id": r.account_id,
            "fetched": r.fetched,
            "added": r.added,
            "error": r.error,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "heartbeat_at": r.heartbeat_at.isoformat() if r.heartbeat_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in runs
    ]
