"""评论抓取门控：从一批作品里选出「够热」的，交给 worker 抓评论。

语义（动态优先，不写死）：
  score = like + comment * weight        # 互动分，评论按 weight 加权
  分位：按 score 降序取前 top_pct        # 唯一「动态」处，随批自适应
  下限：剔除 comment < min_comments 或 like < min_likes  # 绝对够格
  预算：最多 budget 条                    # 别失控
  「不重抓」：已有 CommentFetchState 的作品直接跳过。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CommentFetchState, ContentItem, ContentMetricSnapshot, JobQueue

# 评论抓取覆盖全部采集平台：抖音（浏览器同源）、B站（aid + reply）、
# 快手（REST v2）、小红书（GET + xsec_token）。
# 其余平台不入队——否则 worker 的 handle_fetch_comments 会因无适配器反复失败重试，
# 把一次成功的采集/同步任务拖成 error。新增平台评论能力时在此登记。
COMMENT_PLATFORMS = frozenset({"douyin", "bilibili", "kuaishou", "xhs"})


def _latest_metric(db: Session, content_id: int) -> ContentMetricSnapshot | None:
    return db.scalar(
        select(ContentMetricSnapshot)
        .where(ContentMetricSnapshot.content_id == content_id)
        .order_by(ContentMetricSnapshot.captured_at.desc())
        .limit(1)
    )


def select_targets(
    db: Session,
    contents: list[ContentItem],
    *,
    platform: str,
    settings: dict,
    bypass_gate: bool = False,
) -> list[ContentItem]:
    """返回需要抓评论的作品（已按下限/分位/预算筛选）。

    ``bypass_gate=True`` 时跳过下限/分位/预算筛选，直接返回本平台尚未抓过评论的作品
    （订阅设置 ``with_comments`` 覆盖全局门控；仍受平台白名单与「不重抓」约束）。
    """
    fetched = set(db.scalars(select(CommentFetchState.content_id)).all())

    eligible = [c for c in contents if c.platform == platform and c.id not in fetched]
    if bypass_gate:
        return eligible

    scored: list[tuple[float, ContentItem]] = []
    for c in eligible:
        m = _latest_metric(db, c.id)
        if m is None:
            continue
        if m.comment < settings["min_comments"] or m.like < settings["min_likes"]:
            continue  # 下限：绝对够格
        score = float(m.like) + float(m.comment) * float(settings["weight"])
        scored.append((score, c))

    scored.sort(key=lambda t: t[0], reverse=True)

    n = len(scored)
    if n == 0:
        return []
    take = n if n < 5 else max(1, int(round(n * float(settings["top_pct"]))))
    take = min(take, int(settings["budget"]))
    return [c for _, c in scored[:take]]


def enqueue_comment_jobs(
    db: Session,
    contents: list[ContentItem],
    *,
    platform: str,
    settings: dict,
    bypass_gate: bool = False,
) -> int:
    """选出目标并入队 fetch_comments，返回**本次新建**的 job 条数。

    低优先级(150)，让下载/转录/分析先跑；不阻塞 API。调用方需保证指标快照已落库。
    仅 COMMENT_PLATFORMS 内的平台入队；已被 queue 去重吸收的重复目标不计入返回值。
    ``bypass_gate=True``（订阅 with_comments）跳过门控阈值，但仍按平台白名单与去重处理。
    """
    from app.core.queue import enqueue

    if platform not in COMMENT_PLATFORMS:
        return 0

    targets = select_targets(
        db, contents, platform=platform, settings=settings, bypass_gate=bypass_gate
    )
    # enqueue(dedup=True) 会命中已有 queued/running 的同类 job 并原样返回，需先记录它们，
    # 才能把返回值如实报成「本次新建条数」，而非「目标条数」。
    pending_ids = set(
        db.scalars(
            select(JobQueue.id).where(
                JobQueue.job_type == "fetch_comments",
                JobQueue.status.in_(("queued", "running")),
            )
        ).all()
    )
    created = 0
    for c in targets:
        job = enqueue(db, "fetch_comments", {"content_id": c.id}, priority=150, commit=False)
        if job.id not in pending_ids:
            pending_ids.add(job.id)
            created += 1
    db.commit()
    return created
