"""爆款拆解服务：拼装输入 → 调 LLM → 算 data_context → 落库。

与 `understand_service` 同构：服务层负责 DB 读 / 组装 / 落库，LLM 调用与提示词在
`app/modules/breakdown.py`（纯模块层，不碰 DB）。对外能力：

- `async run_breakdown(db, asset_id, *, force=False) -> ViralBreakdown`：跑完整链路。
- `_data_context(db, content, asset)`：**确定性**计算互动上下文（相对中位数倍数/互动率/分位），
  数字由后端算好交给 LLM 只做定性引用，模型不重算、不杜撰（设计 §2.2 / 红线）。
- `_comment_digest(db, content_id, top_n=30)`：按 like_count 取 Top-N 评论供 LLM 归纳观众洞察。

错误语义（对齐 understand_service）：
- `BreakdownInputError`：素材/内容缺失等数据问题——不可重试，worker 直接失败。
- 其余异常：落 `viral_breakdown.status="error"` + `error` 后**原样抛出**（worker 重试）。
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import events
from app.core.breakdown_settings import get_breakdown_settings
from app.models import (
    Comment,
    ContentItem,
    ContentMetricSnapshot,
    MediaAsset,
    Transcript,
    VideoUnderstanding,
    ViralBreakdown,
)
from app.modules import breakdown

logger = logging.getLogger("platform.breakdown")

_PROMPT_VERSION = "v1"
# 送入拆解的转录分段数（用于拆钩子/时间轴；全文另由 full_text 提供）。
_SEGMENT_LIMIT = 20


class BreakdownInputError(ValueError):
    """输入/数据问题导致拆解不可执行（如资产无关联内容）——不应被当作可重试故障。

    继承 ValueError（调用方/测试可用 ValueError 捕获）。worker 侧据此不重试。
    """


# --------------------------------------------------------------------------- 确定性数字


def _median(values: list[float]) -> float:
    """稳健中位数：空列表返回 0；偶数个取中间两值平均。"""
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return float(vals[mid])
    return (float(vals[mid - 1]) + float(vals[mid])) / 2.0


def _percentile(values: list[float], x: float) -> float:
    """x 在 values 中的分位（0-1）：中位秩法 (小于数 + 0.5×相等数) / 总数。"""
    n = len(values)
    if n == 0:
        return 0.0
    less = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return max(0.0, min(1.0, (less + 0.5 * equal) / n))


def _latest_metrics(db: Session, content_ids: list[int]) -> dict[int, ContentMetricSnapshot]:
    """每个 content 的最新一条互动快照（无 -> 不出现）。"""
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


def _author_content_ids(db: Session, content: ContentItem) -> list[int]:
    """该作者（同平台、同 author_id）的全部内容 id；author_id 为空时退化为本条。"""
    author_id = (content.author_id or "").strip()
    if not author_id:
        return [content.id]
    return list(
        db.scalars(
            select(ContentItem.id).where(
                ContentItem.author_id == author_id,
                ContentItem.platform == content.platform,
            )
        ).all()
    )


def _data_context(db: Session, content: ContentItem, asset: MediaAsset) -> dict:
    """确定性计算互动上下文：当前值 + 相对该作者的倍数 / 互动率 / 分位。

    数字**全部在此计算**（不是 LLM 产出）。除数为 0 时相关倍数 / 比率一律取 0，避免除零。
    """
    ids = _author_content_ids(db, content)
    if content.id not in ids:
        ids.append(content.id)
    latest = _latest_metrics(db, ids)

    me = latest.get(content.id)
    m_play = int(getattr(me, "play", 0) or 0)
    m_like = int(getattr(me, "like", 0) or 0)
    m_comment = int(getattr(me, "comment", 0) or 0)
    m_favorite = int(getattr(me, "favorite", 0) or 0)
    m_share = int(getattr(me, "share", 0) or 0)

    # 该作者全部内容的最新播放/点赞（本条也含在内）。
    plays = [float(getattr(latest.get(i), "play", 0) or 0) for i in ids]
    likes = [float(getattr(latest.get(i), "like", 0) or 0) for i in ids]
    median_play = _median(plays)
    median_like = _median(likes)

    engagement_rate = (
        (m_like + m_comment + m_favorite + m_share) / m_play if m_play else 0.0
    )

    return {
        "play": m_play,
        "like": m_like,
        "comment": m_comment,
        "favorite": m_favorite,
        "share": m_share,
        "median_play": median_play,
        "median_like": median_like,
        "play_vs_median": (m_play / median_play) if median_play else 0.0,
        "like_vs_median": (m_like / median_like) if median_like else 0.0,
        "engagement_rate": engagement_rate,
        "percentile": _percentile(plays, float(m_play)),
    }


# --------------------------------------------------------------------------- 输入拼装


def _comment_digest(db: Session, content_id: int, top_n: int = 30) -> list[dict]:
    """按 like_count 取 Top-N 一级评论（昵称 / 点赞数 / 正文）。"""
    if top_n <= 0:
        return []
    rows = db.scalars(
        select(Comment)
        .where(Comment.content_id == content_id)
        .order_by(Comment.like_count.desc(), Comment.id.asc())
        .limit(top_n)
    ).all()
    return [
        {"nickname": c.nickname, "like_count": c.like_count, "content": c.content}
        for c in rows
    ]


def _comment_lines(digest: list[dict]) -> list[str]:
    """把评论摘要渲染成提示词行：``昵称（N赞）：正文``。"""
    lines = []
    for c in digest:
        body = (c.get("content") or "").strip()
        if not body:
            continue
        lines.append(f"{c.get('nickname') or '匿名'}（{c.get('like_count', 0)}赞）：{body}")
    return lines


def _transcript_text(db: Session, asset_id: int) -> str:
    """转录全文 + 前若干分段（拼成一段文本送入提示词）。"""
    tr = db.scalar(select(Transcript).where(Transcript.asset_id == asset_id))
    if tr is None:
        return ""
    text = (tr.full_text or "").strip()
    segments = tr.segments if isinstance(tr.segments, list) else []
    seg_lines = []
    for seg in segments[:_SEGMENT_LIMIT]:
        if not isinstance(seg, dict):
            continue
        body = str(seg.get("text", "")).strip()
        if not body:
            continue
        seg_lines.append(f"[{seg.get('start', '')}-{seg.get('end', '')}] {body}")
    if seg_lines:
        text = (text + "\n" + "\n".join(seg_lines)).strip()
    return text


def _understanding(db: Session, asset_id: int) -> dict | None:
    """复用 L3 视频理解结果（summary/dimensions/timeline）；无有效内容返回 None。"""
    row = db.scalar(select(VideoUnderstanding).where(VideoUnderstanding.asset_id == asset_id))
    if row is None:
        return None
    if not (row.summary or row.dimensions or row.timeline):
        return None
    return {
        "summary": row.summary or "",
        "dimensions": row.dimensions or {},
        "timeline": row.timeline or [],
        "category": row.category or "",
    }


def _assemble_payload(db: Session, content: ContentItem, asset: MediaAsset, prompt: str) -> dict:
    """把元数据 + 转录 + L3 + 评论 + 确定性数字拼成提示词 payload。"""
    data_context = _data_context(db, content, asset)
    understanding = _understanding(db, asset.id)
    return {
        "prompt": prompt,
        "platform": content.platform,
        "title": content.title,
        "author": content.author_name,
        "duration_sec": content.duration_sec,
        "published_at": content.published_at.isoformat() if content.published_at else None,
        "play": data_context["play"],
        "like": data_context["like"],
        "comment": data_context["comment"],
        "data_context": data_context,
        "transcript": _transcript_text(db, asset.id),
        "timeline": (understanding or {}).get("timeline", []),
        "understanding": understanding,
        "comments": _comment_lines(_comment_digest(db, content.id)),
    }


# --------------------------------------------------------------------------- 结果落库


def _clamp_score(value: object) -> float:
    """把模型评分收敛到 0-100；非法 / NaN 记 0。"""
    if isinstance(value, bool):
        return 0.0
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if num != num:
        return 0.0
    return max(0.0, min(100.0, num))


def _sections(parsed: dict) -> dict:
    """模型定性产出中除 verdict/score/comment_insights 外的各板块。"""
    return {
        "hook": parsed.get("hook"),
        "structure": parsed.get("structure"),
        "pacing": parsed.get("pacing"),
        "script": parsed.get("script"),
        "psych_triggers": parsed.get("psych_triggers"),
        "cta": parsed.get("cta"),
        "blueprint": parsed.get("blueprint"),
        "replication_tips": parsed.get("replication_tips"),
    }


def _str_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _dict_or_empty(value: object) -> dict:
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- 组合服务


async def run_breakdown(db: Session, asset_id: int, *, force: bool = False) -> ViralBreakdown:
    """跑完整爆款拆解链路，返回并落库 `ViralBreakdown`。

    - 已 `done` 且 `force=False`：直接返回既有行（幂等，不重复花 token）。
    - `force=True`：即使已 `done` 也重跑（upsert，按 asset_id 唯一，不产生重复行）。

    Raises:
        BreakdownInputError: 素材不存在 / 无关联内容（数据问题，不可重试）。
        Exception: 拆解链路任何失败——落 `status=error` 后原样抛出（worker 负责重试）。
    """
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise BreakdownInputError(f"素材不存在: {asset_id}")

    row = db.scalar(select(ViralBreakdown).where(ViralBreakdown.asset_id == asset_id))
    if row is not None and row.status == "done" and not force:
        return row

    if row is None:
        row = ViralBreakdown(asset_id=asset_id)
        db.add(row)
    row.status = "doing"
    row.error = ""
    db.commit()
    db.refresh(row)
    events.publish("breakdown_status", {"asset_id": asset_id, "status": "doing"})

    try:
        content = db.get(ContentItem, asset.content_id)
        if content is None:  # 数据不一致（孤儿资产）：显式报错，不静默产出空报告
            raise BreakdownInputError(
                f"素材 #{asset_id} 未关联内容（content_id={asset.content_id}）"
            )

        from app.services.understand_service import _resolve_llm

        base_url, api_key, llm_model = _resolve_llm(db)
        s = get_breakdown_settings(db)

        payload = _assemble_payload(db, content, asset, s["prompt"])
        system, user = breakdown.build_breakdown_prompt(payload)
        result = await breakdown.run_llm(
            system, user, base_url=base_url, api_key=api_key, model=llm_model
        )
        parsed = result.get("data") or {}
        if not isinstance(parsed, dict):
            raise ValueError("拆解返回不是 JSON 对象")

        row.score = _clamp_score(parsed.get("score"))
        row.verdict = _str_or_empty(parsed.get("verdict"))
        row.sections = _sections(parsed)
        row.comment_insights = _dict_or_empty(parsed.get("comment_insights"))
        row.data_context = payload["data_context"]
        row.understanding_available = payload["understanding"] is not None
        row.model = result.get("model") or llm_model
        row.prompt_version = _PROMPT_VERSION
        row.usage = result.get("usage") or {}
        row.status = "done"
        row.error = ""
        db.commit()
        db.refresh(row)
        events.publish("breakdown_status", {"asset_id": asset_id, "status": "done"})
        return row
    except Exception as exc:
        db.rollback()
        row = db.scalar(select(ViralBreakdown).where(ViralBreakdown.asset_id == asset_id))
        if row is None:
            row = ViralBreakdown(asset_id=asset_id)
            db.add(row)
        row.status = "error"
        row.error = str(exc)[:512]
        db.commit()
        events.publish("breakdown_status", {"asset_id": asset_id, "status": "error"})
        raise
