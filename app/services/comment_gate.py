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

from app.models import CommentFetchState, ContentItem, ContentMetricSnapshot


def _latest_metric(db: Session, content_id: int) -> ContentMetricSnapshot | None:
    return db.scalar(
        select(ContentMetricSnapshot)
        .where(ContentMetricSnapshot.content_id == content_id)
        .order_by(ContentMetricSnapshot.captured_at.desc())
        .limit(1)
    )


def select_targets(db: Session, contents: list[ContentItem], *, platform: str, settings: dict) -> list[ContentItem]:
    """返回需要抓评论的作品（已按下限/分位/预算筛选）。"""
    fetched = set(db.scalars(select(CommentFetchState.content_id)).all())

    scored: list[tuple[float, ContentItem]] = []
    for c in contents:
        if c.platform != platform or c.id in fetched:
            continue
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
