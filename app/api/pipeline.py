"""处理队列 API（P3）。

- 队列视图：按状态/类型筛选任务。
- 重试失败任务。
- 队列概览：各状态计数（前端流水线视图用）。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import queue, security
from app.db import get_db
from app.models import ContentItem, JobQueue, MediaAsset, TaskLog

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"], dependencies=[Depends(security.require_admin)])


@router.get("/jobs")
def list_jobs(
    db: Session = Depends(get_db),
    status: str | None = None,
    job_type: str | None = None,
    limit: int = Query(100, ge=1, le=300),
    offset: int = Query(0, ge=0),
):
    stmt = select(JobQueue)
    if status:
        stmt = stmt.where(JobQueue.status == status)
    if job_type:
        stmt = stmt.where(JobQueue.job_type == job_type)
    stmt = stmt.order_by(JobQueue.id.desc()).limit(limit).offset(offset)
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
            .where(MediaAsset.id.in_(asset_ids), MediaAsset.deleted_at.is_(None))
        ).all()
        asset_titles = {aid: (title or "") for aid, title in rows}
    if content_ids:
        rows = db.execute(
            select(ContentItem.id, ContentItem.title).where(ContentItem.id.in_(content_ids))
        ).all()
        content_titles = {cid: (title or "") for cid, title in rows}

    out = []
    from app.core.ai_schedule import AI_JOB_TYPES, ai_schedule_status, get_ai_schedule

    schedule_status = ai_schedule_status(get_ai_schedule(db))
    ai_open = bool(schedule_status["is_open"])
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
                # AI 任务在时段外保留 queued；给队列页一个明确的“等待窗口”标记。
                "ai_schedule_held": bool(
                    j.job_type in AI_JOB_TYPES and j.status == "queued" and not ai_open
                ),
                "ai_next_open_at": schedule_status.get("next_open_at"),
                "ai_next_open_local": schedule_status.get("next_open_local"),
                "updated_at": j.updated_at.isoformat() if j.updated_at else None,
            }
        )
    return out


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    """轻量任务状态端点：手动操作提交后，前端只轮询这一条任务。"""
    job = db.get(JobQueue, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    payload = job.payload or {}
    run_id = None
    if job.job_type == "sync" and payload.get("subscription_id") is not None:
        from app.models import SyncRun

        run_id = db.scalar(
            select(SyncRun.id)
            .where(SyncRun.job_id == job.id)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
    elif job.job_type == "crawl" and payload.get("task_id") is not None:
        from app.models import CrawlRun

        run_id = db.scalar(
            select(CrawlRun.id)
            .where(CrawlRun.job_id == job.id)
            .order_by(CrawlRun.id.desc())
            .limit(1)
        )
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "error": job.error,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "run_id": run_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.get("/jobs/{job_id}/logs")
def get_job_logs(
    job_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=500),
    after_id: int = Query(0, ge=0),
):
    """Structured task timeline for one queue job.

    ``after_id`` makes the endpoint usable by a live task drawer without
    re-reading the whole history on every poll.
    """
    if db.get(JobQueue, job_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    rows = db.scalars(
        select(TaskLog)
        .where(TaskLog.job_id == job_id, TaskLog.id > after_id)
        .order_by(TaskLog.id)
        .limit(limit)
    ).all()
    return {
        "job_id": job_id,
        "logs": [
            {
                "id": row.id,
                "level": row.level,
                "message": row.message,
                "detail": row.detail or {},
                "actor": row.actor,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


@router.get("/assets/{asset_id}")
def get_asset_workflow(asset_id: int, db: Session = Depends(get_db)):
    """Expose the durable processing DAG for one asset.

    The UI uses this instead of inferring prerequisites from a single queue job;
    a step can legitimately be ``waiting_prerequisite`` with no job id yet.
    """
    from app.services.workflow_engine import public_status
    from app.services.storage_lifecycle import get_live_asset

    if get_live_asset(db, asset_id) is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    # An asset that has never entered the workflow is a valid empty state, not
    # a missing resource.  Returning ``null`` forced every caller to special
    # case the shape and made the UI dereference ``steps`` on legacy assets.
    return public_status(db, asset_id) or {
        "run_id": None,
        "asset_id": asset_id,
        "kind": "content_processing",
        "status": "not_started",
        "error": "",
        "created_at": None,
        "finished_at": None,
        "steps": [],
    }


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


@router.get("/alerts")
def list_alerts(
    db: Session = Depends(get_db),
    status: str | None = None,
    limit: int = Query(100, ge=1, le=300),
):
    from app.services import alerts

    rows = alerts.list_alerts(db, status=status, limit=limit)
    return {
        "active": alerts.active_count(db),
        "alerts": [
            {
                "id": row.id,
                "kind": row.kind,
                "severity": row.severity,
                "status": row.status,
                "title": row.title,
                "message": row.message,
                "job_id": row.job_id,
                "job_type": row.job_type,
                "occurrences": row.occurrences,
                "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            }
            for row in rows
        ],
    }


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, db: Session = Depends(get_db)):
    from app.services import alerts

    row = alerts.acknowledge(db, alert_id)
    if row is None:
        raise HTTPException(status_code=400, detail="告警不存在或已处理")
    return {"id": row.id, "status": row.status}


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    from app.services import alerts

    row = alerts.resolve(db, alert_id)
    if row is None:
        raise HTTPException(status_code=400, detail="告警不存在或已恢复")
    return {"id": row.id, "status": row.status}


@router.get("/ai-schedule")
def pipeline_ai_schedule(db: Session = Depends(get_db)):
    """队列页展示的 AI 时段状态（与系统设置读写同一份配置）。"""
    from app.core.ai_schedule import ai_schedule_status, get_ai_schedule

    return ai_schedule_status(get_ai_schedule(db))


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db)):
    job = queue.retry(db, job_id)
    if job is None:
        raise HTTPException(status_code=400, detail="任务不存在或不是失败状态，无法重试")
    from app.services.workflow_engine import retry_step

    retry_step(db, job)
    db.commit()
    return {"id": job.id, "status": job.status}


@router.post("/jobs/retry-failed")
def retry_all_failed(db: Session = Depends(get_db), job_type: str | None = None):
    """批量重试所有失败任务。"""
    ids = queue.retry_failed(db, job_type=job_type)
    if ids:
        from app.models import PipelineStep
        from app.services.workflow_engine import retry_step

        rows = db.scalars(select(JobQueue).where(JobQueue.id.in_(ids))).all()
        for job in rows:
            if db.scalar(select(PipelineStep.id).where(PipelineStep.job_id == job.id)) is not None:
                retry_step(db, job)
        db.commit()
    return {"retried": len(ids)}
