"""数据看板（P6）+ 总览（P2+）。

- 总览：账号异常数、今日抓取、待处理/失败任务、磁盘、热门 Top。
- 看板：作者维度、关键词维度走势（靠 content_metric_snapshot 的历史快照）。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core import security
from app.db import get_db
from app.models import (
    ContentItem,
    ContentMetricSnapshot,
    CrawlRun,
    JobQueue,
    MediaAsset,
    PlatformAccount,
)

router = APIRouter(prefix="/api", tags=["analytics"], dependencies=[Depends(security.require_admin)])


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    accounts_total = db.scalar(select(func.count(PlatformAccount.id))) or 0
    accounts_bad = (
        db.scalar(select(func.count(PlatformAccount.id)).where(PlatformAccount.status.in_(("expired", "error"))))
        or 0
    )
    today_content = db.scalar(select(func.count(ContentItem.id)).where(ContentItem.created_at >= today)) or 0
    jobs_pending = (
        db.scalar(select(func.count(JobQueue.id)).where(JobQueue.status.in_(("queued", "running")))) or 0
    )
    jobs_failed = db.scalar(select(func.count(JobQueue.id)).where(JobQueue.status == "error")) or 0
    assets_total = db.scalar(select(func.count(MediaAsset.id))) or 0

    # 最近 24h 热门 Top10（按最新快照的播放量）
    since = datetime.utcnow() - timedelta(hours=24)
    hot_rows = db.execute(
        select(ContentItem.title, ContentItem.platform, ContentItem.author_name, func.max(ContentMetricSnapshot.play))
        .join(ContentMetricSnapshot, ContentMetricSnapshot.content_id == ContentItem.id)
        .where(ContentMetricSnapshot.captured_at >= since)
        .group_by(ContentItem.id, ContentItem.title, ContentItem.platform, ContentItem.author_name)
        .order_by(desc(func.max(ContentMetricSnapshot.play)))
        .limit(10)
    ).all()
    hot = [{"title": t, "platform": p, "author": a, "play": play or 0} for t, p, a, play in hot_rows]

    return {
        "accounts": {"total": accounts_total, "abnormal": accounts_bad},
        "today_crawled": today_content,
        "jobs": {"pending": jobs_pending, "failed": jobs_failed},
        "assets_total": assets_total,
        "hot": hot,
    }


@router.get("/analytics/creators")
def analytics_creators(db: Session = Depends(get_db), limit: int = 20):
    """作者维度：发布量 + 最新累计互动。"""
    rows = db.execute(
        select(
            ContentItem.author_id,
            ContentItem.author_name,
            ContentItem.platform,
            func.count(ContentItem.id).label("cnt"),
        )
        .where(ContentItem.author_id != "")
        .group_by(ContentItem.author_id, ContentItem.author_name, ContentItem.platform)
        .order_by(desc("cnt"))
        .limit(limit)
    ).all()
    return [
        {"author_id": aid, "author_name": name, "platform": plat, "content_count": cnt}
        for aid, name, plat, cnt in rows
    ]


@router.get("/analytics/keywords")
def analytics_keywords(db: Session = Depends(get_db), limit: int = 20):
    """关键词维度：各采集任务近期新增内容量。"""
    rows = db.execute(
        select(CrawlRun.task_id, func.sum(CrawlRun.added).label("added"), func.count(CrawlRun.id).label("runs"))
        .group_by(CrawlRun.task_id)
        .order_by(desc("added"))
        .limit(limit)
    ).all()
    return [{"task_id": tid, "total_added": added or 0, "runs": runs} for tid, added, runs in rows]


@router.get("/analytics/content/{content_id}/trend")
def content_trend(content_id: int, db: Session = Depends(get_db)):
    """单条内容的互动走势（画趋势图用）。"""
    snaps = db.scalars(
        select(ContentMetricSnapshot)
        .where(ContentMetricSnapshot.content_id == content_id)
        .order_by(ContentMetricSnapshot.captured_at)
    ).all()
    return [
        {
            "captured_at": s.captured_at.isoformat(),
            "play": s.play,
            "like": s.like,
            "comment": s.comment,
            "favorite": s.favorite,
            "share": s.share,
        }
        for s in snaps
    ]
