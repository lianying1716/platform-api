"""数据看板（P6）+ 总览（P2+）。

- 总览：账号异常数、今日抓取、待处理/失败任务、磁盘、热门 Top。
- 看板：作者维度、关键词维度走势（靠 content_metric_snapshot 的历史快照）。
- 对标账号报告：单个作者的内容策略聚合（概览/发布节奏/Top/爆款规律/内容支柱/对标基准/缺口）。
"""

from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core import security
from app.core.breakdown_settings import get_breakdown_settings
from app.db import get_db
from app.models import (
    ContentItem,
    ContentMetricSnapshot,
    CrawlRun,
    CreatorProfile,
    JobQueue,
    MediaAsset,
    PlatformAccount,
    StorageRoot,
    VideoUnderstanding,
)

router = APIRouter(prefix="/api", tags=["analytics"], dependencies=[Depends(security.require_admin)])


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db), account_id: int | None = None):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)

    content_filter = [ContentItem.account_id == account_id] if account_id is not None else []
    asset_filter = content_filter

    accounts_total = (
        db.scalar(
            select(func.count(PlatformAccount.id)).where(
                PlatformAccount.is_system.is_(False),
                *([PlatformAccount.id == account_id] if account_id is not None else []),
            )
        )
        or 0
    )
    accounts_bad = (
        db.scalar(
            select(func.count(PlatformAccount.id)).where(
                PlatformAccount.is_system.is_(False),
                PlatformAccount.status.in_(("expired", "error")),
                *([PlatformAccount.id == account_id] if account_id is not None else []),
            )
        )
        or 0
    )
    today_content = (
        db.scalar(select(func.count(ContentItem.id)).where(ContentItem.created_at >= today, *content_filter)) or 0
    )
    week_content = (
        db.scalar(select(func.count(ContentItem.id)).where(ContentItem.created_at >= week_ago, *content_filter))
        or 0
    )
    jobs_pending = (
        db.scalar(select(func.count(JobQueue.id)).where(JobQueue.status.in_(("queued", "running")))) or 0
    )
    jobs_failed = db.scalar(select(func.count(JobQueue.id)).where(JobQueue.status == "error")) or 0
    assets_total = (
        db.scalar(
            select(func.count(MediaAsset.id))
            .join(ContentItem, MediaAsset.content_id == ContentItem.id)
            .where(*asset_filter)
        )
        or 0
    )

    # 内容类型分布：视频 / 文章 / 图文
    by_type: dict[str, int] = {"video": 0, "article": 0, "image": 0}
    for ctype, cnt in db.execute(
        select(ContentItem.content_type, func.count(ContentItem.id))
        .where(*content_filter)
        .group_by(ContentItem.content_type)
    ).all():
        by_type[str(ctype or "video")] = int(cnt)
    video_total = by_type.get("video", 0)
    article_total = by_type.get("article", 0) + by_type.get("image", 0)

    # 总时长：只统计视频（文章/图文没有时长概念）
    duration_total = (
        db.scalar(
            select(func.coalesce(func.sum(ContentItem.duration_sec), 0)).where(
                ContentItem.content_type == "video",
                *content_filter,
            )
        )
        or 0
    )

    # 平台维度：条数 / 视频 / 文章 / 播放 / 点赞
    platform_rows = db.execute(
        select(
            ContentItem.platform,
            ContentItem.content_type,
            func.count(ContentItem.id),
        )
        .where(*content_filter)
        .group_by(ContentItem.platform, ContentItem.content_type)
    ).all()
    platforms: dict[str, dict] = {}
    for plat, ctype, cnt in platform_rows:
        p = platforms.setdefault(
            plat or "unknown",
            {
                "platform": plat or "unknown", "total": 0, "video": 0, "article": 0,
                "play": 0, "like": 0, "transcribed": 0, "categorized": 0,
            },
        )
        p["total"] += int(cnt)
        if (ctype or "video") == "video":
            p["video"] += int(cnt)
        else:
            p["article"] += int(cnt)

    # 每个内容取最新快照的播放/点赞，按平台聚合（子查询取最新 captured_at）
    latest = (
        select(
            ContentMetricSnapshot.content_id,
            func.max(ContentMetricSnapshot.captured_at).label("ts"),
        )
        .group_by(ContentMetricSnapshot.content_id)
        .subquery()
    )
    metric_rows = db.execute(
        select(
            ContentItem.platform,
            func.coalesce(func.sum(ContentMetricSnapshot.play), 0),
            func.coalesce(func.sum(ContentMetricSnapshot.like), 0),
            func.coalesce(func.sum(ContentMetricSnapshot.comment), 0),
            func.coalesce(func.sum(ContentMetricSnapshot.favorite), 0),
        )
        .join(ContentMetricSnapshot, ContentMetricSnapshot.content_id == ContentItem.id)
        .join(latest, (latest.c.content_id == ContentMetricSnapshot.content_id) & (latest.c.ts == ContentMetricSnapshot.captured_at))
        .where(*content_filter)
        .group_by(ContentItem.platform)
    ).all()
    totals = {"play": 0, "like": 0, "comment": 0, "favorite": 0}
    for plat, play, like, comment, fav in metric_rows:
        p = platforms.setdefault(
            plat or "unknown",
            {
                "platform": plat or "unknown", "total": 0, "video": 0, "article": 0,
                "play": 0, "like": 0, "transcribed": 0, "categorized": 0,
            },
        )
        p["play"] = int(play or 0)
        p["like"] = int(like or 0)
        totals["play"] += int(play or 0)
        totals["like"] += int(like or 0)
        totals["comment"] += int(comment or 0)
        totals["favorite"] += int(fav or 0)

    # 每平台加工进度：已转录（media_asset.transcribe_status=done）/ 已判类（auto_category 非空）。
    transcribed_rows = db.execute(
        select(ContentItem.platform, func.count(MediaAsset.id))
        .join(MediaAsset, MediaAsset.content_id == ContentItem.id)
        .where(MediaAsset.transcribe_status == "done", *content_filter)
        .group_by(ContentItem.platform)
    ).all()
    for plat, cnt in transcribed_rows:
        platforms.setdefault(plat or "unknown", {"platform": plat or "unknown"})["transcribed"] = int(cnt or 0)
    categorized_rows = db.execute(
        select(ContentItem.platform, func.count(ContentItem.id))
        .where(ContentItem.auto_category != "", *content_filter)
        .group_by(ContentItem.platform)
    ).all()
    for plat, cnt in categorized_rows:
        platforms.setdefault(plat or "unknown", {"platform": plat or "unknown"})["categorized"] = int(cnt or 0)

    # 分类分布（auto_category 判类结果）
    from app.core.process_settings import get_taxonomy

    taxonomy = get_taxonomy(db)
    category_rows = db.execute(
        select(ContentItem.auto_category, func.count(ContentItem.id))
        .where(*content_filter)
        .group_by(ContentItem.auto_category)
        .order_by(desc(func.count(ContentItem.id)))
    ).all()
    categories = [
        {
            "key": key or "",
            # 判类结果可能是枚举 key（走 taxonomy 翻译）或已是中文标签（直接用）
            "label": (taxonomy.get(key) or key) if key else "未判类",
            "count": int(cnt),
        }
        for key, cnt in category_rows
    ]

    # 加工进度
    transcribed = (
        db.scalar(
            select(func.count(MediaAsset.id))
            .join(ContentItem, MediaAsset.content_id == ContentItem.id)
            .where(MediaAsset.transcribe_status == "done", *asset_filter)
        )
        or 0
    )
    understood = (
        db.scalar(
            select(func.count(MediaAsset.id))
            .join(ContentItem, MediaAsset.content_id == ContentItem.id)
            .where(MediaAsset.understand_status == "done", *asset_filter)
        )
        or 0
    )

    # 存储占用：按素材累加本地文件体积（文件不存在计 0）
    from app.core import storage

    used_bytes = 0
    storage_by_platform: dict[str, int] = {}
    for asset, content in db.execute(
        select(MediaAsset, ContentItem).join(ContentItem, MediaAsset.content_id == ContentItem.id)
        .where(*asset_filter)
    ).all():
        size = storage.file_size(storage.resolve_asset_file(db, asset)) if asset.video_file else 0
        if not size and asset.relative_path:
            size = storage.dir_size(storage.resolve_asset_dir(db, asset))
        used_bytes += size
        key = content.platform or "unknown"
        storage_by_platform[key] = storage_by_platform.get(key, 0) + size

    roots = db.scalars(select(StorageRoot).order_by(StorageRoot.id)).all()
    storage_roots = []
    for r in roots:
        ok, detail = storage.probe_root(r.path)
        storage_roots.append(
            {
                "id": r.id,
                "name": r.name,
                "path": r.path,
                "is_default": r.is_default,
                "available": ok,
                "detail": detail,
                "used_bytes": storage.dir_size(r.path) if ok else 0,
            }
        )

    # 近 14 天每日新增
    trend = []
    for i in range(13, -1, -1):
        day = today - timedelta(days=i)
        nxt = day + timedelta(days=1)
        cnt = (
            db.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.created_at >= day, ContentItem.created_at < nxt, *content_filter
                )
            )
            or 0
        )
        trend.append({"date": day.strftime("%m-%d"), "added": int(cnt)})

    # 最近 24h 热门 Top10（按最新快照的播放量）
    since = datetime.utcnow() - timedelta(hours=24)
    hot_rows = db.execute(
        select(ContentItem.title, ContentItem.platform, ContentItem.author_name, func.max(ContentMetricSnapshot.play))
        .join(ContentMetricSnapshot, ContentMetricSnapshot.content_id == ContentItem.id)
        .where(ContentMetricSnapshot.captured_at >= since, *content_filter)
        .group_by(ContentItem.id, ContentItem.title, ContentItem.platform, ContentItem.author_name)
        .order_by(desc(func.max(ContentMetricSnapshot.play)))
        .limit(10)
    ).all()
    hot = [{"title": t, "platform": p, "author": a, "play": play or 0} for t, p, a, play in hot_rows]

    return {
        "accounts": {"total": accounts_total, "abnormal": accounts_bad},
        # 兼容旧前端字段
        "accounts_error": accounts_bad,
        "crawled_today": int(today_content),
        "jobs_pending": int(jobs_pending),
        "jobs_failed": int(jobs_failed),
        "hot_assets": hot,
        # 新增统计
        "today_crawled": today_content,
        "week_crawled": int(week_content),
        "jobs": {"pending": jobs_pending, "failed": jobs_failed},
        "assets_total": assets_total,
        "content": {
            "total": sum(by_type.values()),
            "video": video_total,
            "article": article_total,
            "duration_sec": int(duration_total),
        },
        "platforms": sorted(platforms.values(), key=lambda x: -x["total"]),
        "categories": categories,
        "processing": {
            "transcribed": int(transcribed),
            "understood": int(understood),
            "pending": int(jobs_pending),
            "failed": int(jobs_failed),
        },
        "engagement": totals,
        "storage": {
            "used_bytes": used_bytes,
            "by_platform": storage_by_platform,
            "roots": storage_roots,
        },
        "trend": trend,
        "hot": hot,
    }


@router.get("/analytics/creators")
def analytics_creators(db: Session = Depends(get_db), limit: int = 20, account_id: int | None = None):
    """作者维度：发布量 + 最新累计互动。优先补 creator_profile 的粉丝/认证信息。"""
    stmt = select(
            ContentItem.author_id,
            ContentItem.author_name,
            ContentItem.platform,
            func.count(ContentItem.id).label("cnt"),
        )
    stmt = stmt.where(ContentItem.author_id != "")
    if account_id is not None:
        stmt = stmt.where(ContentItem.account_id == account_id)
    stmt = (
        stmt.group_by(ContentItem.author_id, ContentItem.author_name, ContentItem.platform)
        .order_by(desc("cnt"))
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    profiles = {
        (p.platform, p.author_id): p
        for p in db.scalars(
            select(CreatorProfile).where(
                CreatorProfile.author_id.in_([aid for aid, *_ in rows]) if rows else False
            )
        ).all()
    }
    out = []
    for aid, name, plat, cnt in rows:
        p = profiles.get((plat, aid))
        out.append(
            {
                "author_id": aid,
                "author_name": (p.author_name if p and p.author_name else name),
                "platform": plat,
                "content_count": cnt,
                "avatar": p.avatar if p else "",
                "follower_count": p.follower_count if p else 0,
                "verified": p.verified if p else False,
                "profile_id": p.id if p else None,
            }
        )
    return out


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


# ============================================================
# 对标账号拆解（实时聚合，不调 LLM、不新增表）
# ============================================================

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_PLATFORM_SCAN_LIMIT = 5000  # 平台基准/缺口扫描上限，避免超大库拖垮实时报告


def _median(values: list[float]) -> float:
    """稳健中位数：空列表 0；偶数个取中间两值平均。"""
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return float(vals[mid])
    return (float(vals[mid - 1]) + float(vals[mid])) / 2.0


def _latest_snapshots(db: Session, content_ids: list[int]) -> dict[int, ContentMetricSnapshot]:
    """每个 content 的最新互动快照（无 -> 不出现）。"""
    if not content_ids:
        return {}
    rows = db.scalars(
        select(ContentMetricSnapshot)
        .where(ContentMetricSnapshot.content_id.in_(content_ids))
        .order_by(
            ContentMetricSnapshot.content_id,
            ContentMetricSnapshot.captured_at.desc(),
            ContentMetricSnapshot.id.desc(),
        )
    ).all()
    latest: dict[int, ContentMetricSnapshot] = {}
    for row in rows:
        latest.setdefault(row.content_id, row)
    return latest


def _category_map(db: Session, content_ids: list[int]) -> dict[int, str]:
    """content_id -> L3 判类（video_understanding.category）；缺 / other 不收录，调用方回退 auto_category。"""
    if not content_ids:
        return {}
    asset_rows = db.execute(
        select(MediaAsset.id, MediaAsset.content_id).where(MediaAsset.content_id.in_(content_ids))
    ).all()
    asset_ids = [aid for aid, _ in asset_rows]
    ua = (
        dict(
            db.execute(
                select(VideoUnderstanding.asset_id, VideoUnderstanding.category).where(
                    VideoUnderstanding.asset_id.in_(asset_ids)
                )
            ).all()
        )
        if asset_ids
        else {}
    )
    out: dict[int, str] = {}
    for aid, cid in asset_rows:
        cat = ua.get(aid)
        if cat and cat != "other" and cid not in out:
            out[cid] = cat
    return out


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@router.get("/analytics/creator/{author_id}/report")
def creator_report(author_id: str, platform: str | None = None, db: Session = Depends(get_db)):
    """对标账号拆解：单个作者的内容策略聚合（确定性、实时计算）。

    板块：账号概览 / 发布节奏 / Top 作品 / 爆款规律 / 内容支柱 / 对标基准 / 缺口提示。
    爆款 = 播放 ≥ 该账号自身中位数 × 阈值（阈值来自 breakdown 配置，避免大号普通内容误判）。
    platform 省略时取该作者内容最多的平台（一个作者的快照口径必须一致）。
    """
    items = list(db.scalars(select(ContentItem).where(ContentItem.author_id == author_id)).all())
    if platform:
        items = [c for c in items if c.platform == platform]
    if not items:
        raise HTTPException(status_code=404, detail="未找到该作者内容")
    if not platform:
        platform = Counter(c.platform for c in items).most_common(1)[0][0]
        items = [c for c in items if c.platform == platform]

    content_ids = [c.id for c in items]
    latest = _latest_snapshots(db, content_ids)
    cat_map = _category_map(db, content_ids)
    viral_multiple = get_breakdown_settings(db)["viral_multiple"]
    author_name = next((c.author_name for c in items if c.author_name), "")
    by_id = {c.id: c for c in items}

    # 中位数口径：只统计「有快照」的内容，未拍到的作品不以 0 计入分母（否则拉低中位数、虚高倍数）。
    snapshot_ids = [cid for cid in content_ids if cid in latest]
    plays = [float(latest[cid].play or 0) for cid in snapshot_ids]
    median_play = _median(plays)

    asset_rows = db.execute(
        select(MediaAsset.id, MediaAsset.content_id).where(MediaAsset.content_id.in_(content_ids))
    ).all()
    asset_by_content: dict[int, int] = {}
    for aid, cid in asset_rows:
        asset_by_content.setdefault(cid, aid)

    def category_of(c: ContentItem) -> str:
        return cat_map.get(c.id) or c.auto_category or "未分类"

    def row_of(c: ContentItem) -> dict:
        m = latest.get(c.id)
        play = int(getattr(m, "play", 0) or 0)
        like = int(getattr(m, "like", 0) or 0)
        comment = int(getattr(m, "comment", 0) or 0)
        favorite = int(getattr(m, "favorite", 0) or 0)
        share = int(getattr(m, "share", 0) or 0)
        return {
            "content_id": c.id,
            "asset_id": asset_by_content.get(c.id),
            "title": c.title,
            "url": c.url,
            "platform": c.platform,
            "published_at": c.published_at.isoformat() if c.published_at else None,
            "duration_sec": c.duration_sec,
            "category": category_of(c),
            "play": play,
            "like": like,
            "comment": comment,
            "favorite": favorite,
            "share": share,
            "engagement_rate": (like + comment + favorite + share) / play if play else 0.0,
            "viral": bool(median_play and play >= median_play * viral_multiple),
        }

    rows = [row_of(c) for c in items]
    n = len(items)

    published = [(c, c.published_at) for c in items if c.published_at]
    timespan = {}
    if published:
        times = [t for _, t in published]
        timespan = {"start": min(times).isoformat(), "end": max(times).isoformat()}

    overview = {
        "content_count": n,
        "snapshot_count": len(snapshot_ids),
        "avg_play": _avg(plays),
        "median_play": median_play,
        # 均值口径与 avg_play 一致：只统计有快照的内容，避免无快照作品以 0 拉低均值。
        "avg_like": _avg([float(latest[cid].like or 0) for cid in snapshot_ids]),
        "avg_comment": _avg([float(latest[cid].comment or 0) for cid in snapshot_ids]),
        "avg_engagement_rate": _avg(
            [
                (
                    float(latest[cid].like or 0)
                    + float(latest[cid].comment or 0)
                    + float(latest[cid].favorite or 0)
                    + float(latest[cid].share or 0)
                )
                / float(latest[cid].play)
                if latest[cid].play
                else 0.0
                for cid in snapshot_ids
            ]
        ),
        "timespan": timespan,
    }

    # 发布节奏：按星期几 / 按小时（找发文习惯）。
    weekday = [0] * 7
    hour = [0] * 24
    for _, t in published:
        weekday[t.weekday()] += 1
        hour[t.hour] += 1
    cadence = {
        "by_weekday": [{"weekday": _WEEKDAYS[i], "count": weekday[i]} for i in range(7)],
        "by_hour": [{"hour": h, "count": hour[h]} for h in range(24)],
        "best_hours": sorted([h for h in range(24) if hour[h]], key=lambda h: -hour[h])[:3],
    }

    top = sorted(rows, key=lambda r: r["play"], reverse=True)[:10]

    # 爆款规律：爆款的类型分布 / 时长 / 发布时段共性。
    virals = [r for r in rows if r["viral"]]
    viral_hours: Counter[int] = Counter(
        by_id[r["content_id"]].published_at.hour
        for r in virals
        if by_id[r["content_id"]].published_at is not None
    )
    viral_rules = {
        "threshold_multiple": viral_multiple,
        "count": len(virals),
        "type_distribution": dict(Counter(r["category"] for r in virals)),
        "avg_duration_sec": _avg([float(r["duration_sec"] or 0) for r in virals]),
        "avg_play": _avg([float(r["play"]) for r in virals]),
        "top_hours": [h for h, _ in viral_hours.most_common(3)],
        "items": virals,
    }

    # 内容支柱：该账号内容类型分布（L3 判类优先，回退 auto_category）。
    pillar = Counter(r["category"] for r in rows)
    content_pillars = [
        {"category": k, "count": v, "share": v / n} for k, v in pillar.most_common()
    ]

    # 对标基准：该账号 vs 同平台全体（各自内容的最新播放中位数）。
    platform_items = list(
        db.scalars(
            select(ContentItem)
            .where(ContentItem.platform == platform)
            .order_by(ContentItem.id.desc())  # 取最近的内容做基准，避免大库下只扫到最旧一批而失真
            .limit(_PLATFORM_SCAN_LIMIT)
        ).all()
    )
    platform_ids = [c.id for c in platform_items]
    plat_latest = _latest_snapshots(db, platform_ids)
    plat_snapshot_ids = [cid for cid in platform_ids if cid in plat_latest]
    plat_plays = [float(plat_latest[cid].play or 0) for cid in plat_snapshot_ids]
    platform_median = _median(plat_plays)
    benchmark = {
        "platform": platform,
        "author_median_play": median_play,
        "platform_median_play": platform_median,
        "author_vs_platform": (median_play / platform_median) if platform_median else 0.0,
        "platform_content_count": len(platform_ids),
        "platform_snapshot_count": len(plat_snapshot_ids),
    }

    # 缺口提示：平台整体「高热度类型」（中位播放 ≥ 平台整体中位数）里，该账号没做的类型。
    plat_cat_map = _category_map(db, platform_ids)
    plat_by_id = {c.id: c for c in platform_items}
    plat_cat_plays: dict[str, list[float]] = {}
    for cid in plat_snapshot_ids:
        c = plat_by_id.get(cid)
        if c is None:
            continue
        cat = plat_cat_map.get(cid) or c.auto_category or "未分类"
        plat_cat_plays.setdefault(cat, []).append(float(plat_latest[cid].play or 0))
    plat_cat_median = {k: _median(v) for k, v in plat_cat_plays.items()}
    base = platform_median or _median([v for v in plat_cat_median.values()])
    author_cats = set(pillar.keys())
    gaps = [
        {"category": k, "platform_median_play": med, "author_done": False}
        for k, med in sorted(plat_cat_median.items(), key=lambda kv: -kv[1])
        if med >= base and k != "未分类" and k not in author_cats
    ]

    return {
        "author_id": author_id,
        "author_name": author_name,
        "platform": platform,
        "overview": overview,
        "cadence": cadence,
        "top": top,
        "viral_rules": viral_rules,
        "content_pillars": content_pillars,
        "benchmark": benchmark,
        "gaps": gaps,
    }
