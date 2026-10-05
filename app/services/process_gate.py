"""素材加工分档门控：从一批作品里决定各自加工档位（l1 转录 / l2 抽帧 / l3 视频理解）。

动态优先、绝不写死（沿用 `comment_gate.py` 范式，阈值来自 `process_settings`）：
  score = w_like*like + w_comment*comment + w_play*play   # 热度分
  下限：like < min_likes_floor → 不高于 l1                  # 绝对不够格
  档位：score >= l3_min_heat → l3；>= l2_min_heat → l2；否则 l1
  分位：按 score 降序，只有前 top_pct 份额可高于 l1          # 随批自适应
  预算：l2/l3 合计受 budget 约束，超出的从最低分起降回 l1
  不降级：已 l3 的候选保持 l3，分数/分位/预算都不降

职责划分（对应设计 §6.1「动态门控决定是否升级」）：
- `select_tier`：纯函数，只做档位决策（不碰 DB）。
- `select_targets` / `enqueue_process_jobs`：把决策接到采集/同步/下载链路上——取最新指标快照
  作为候选，按档位自动入队 `extract_frames`(l2) / `understand_video`(l3)，并去重、跳过已加工者。
  仅对**已下载视频**的素材入队：抽帧与视频理解都要读本地视频，未下载就入队只会失败重试。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContentItem, ContentMetricSnapshot, JobQueue, MediaAsset

# 档位从低到高；"" 表示尚未加工。
_RANK = {"": 0, "l1": 1, "l2": 2, "l3": 3}

# 档位 -> 落地 job 类型。l2 抽帧（零模型成本）、l3 视频理解（一次视频模型调用）。
_TIER_JOB = {"l2": "extract_frames", "l3": "understand_video"}
# 分档加工优先级：低于核心管线（download/transcribe/analyze=100），高于评论富集(150)。
_PROCESS_PRIORITY = 120


def _score(c: dict, s: dict) -> float:
    """热度分：加权求和（缺失指标按 0）。"""
    return (
        float(s["weight_like"]) * float(c.get("like") or 0)
        + float(s["weight_comment"]) * float(c.get("comment") or 0)
        + float(s["weight_play"]) * float(c.get("play") or 0)
    )


def _threshold_tier(score: float, s: dict) -> str:
    if score >= float(s["l3_min_heat"]):
        return "l3"
    if score >= float(s["l2_min_heat"]):
        return "l2"
    return "l1"


def _percentile_take(n: int, top_pct: float) -> int:
    """前 top_pct 份额可高于 l1 的名额（整数分位，避免浮点 off-by-one）。

    `round(0.2*5)` 之类的浮点误差会多放行一条；这里以万分位整数做 ceil，
    并至少放行 1 条——分位是「上界」而非「门槛」，最优者够格仍应可升级。
    """
    if n <= 0:
        return 0
    pct = float(top_pct)
    if pct >= 1.0:
        return n
    if pct <= 0.0:
        return 1
    num = int(round(pct * 10000))
    return max(1, -(-(n * num) // 10000))  # ceil(n*num/10000)


def select_tier(candidates: list[dict], s: dict) -> list[dict]:
    """为每个候选决定加工档位，返回等长结果（含 score / reason）。

    输入候选：`{"content_id", "like", "comment", "play", "existing_tier"}`。
    输出：`{"content_id", "tier", "score", "reason"}`，顺序与输入一致。
    """
    n = len(candidates)
    if n == 0:
        return []

    floor = float(s["min_likes_floor"])
    take = _percentile_take(n, s.get("top_pct", 1.0))
    budget = int(s["budget"])

    scored = [_score(c, s) for c in candidates]
    order = sorted(range(n), key=lambda i: scored[i], reverse=True)
    rank = {idx: r for r, idx in enumerate(order)}

    entries: list[dict] = []
    for i, c in enumerate(candidates):
        like = float(c.get("like") or 0)
        existing = str(c.get("existing_tier") or "")
        locked = existing == "l3"  # 已 l3 不降级

        if locked:
            tier = "l3"
            reason = "已 l3（不降级）"
        else:
            tier = _threshold_tier(scored[i], s)
            reason = ""
            if like < floor:
                tier = "l1"
                reason = f"点赞 {like:g} < 下限 {floor:g}"
            elif rank[i] >= take:
                tier = "l1"
                reason = f"未入前 {float(s.get('top_pct', 1.0)):.0%} 热度分位"
            elif tier == "l3":
                reason = f"热度 {scored[i]:g} ≥ L3 阈值 {float(s['l3_min_heat']):g}"
            elif tier == "l2":
                reason = f"热度 {scored[i]:g} ≥ L2 阈值 {float(s['l2_min_heat']):g}"
            else:
                reason = f"热度 {scored[i]:g} 不足 L2 阈值 {float(s['l2_min_heat']):g}"

        entries.append(
            {
                "content_id": c.get("content_id"),
                "tier": tier,
                "score": scored[i],
                "reason": reason,
                "_locked": locked,
            }
        )

    # 预算：l2/l3 合计超过 budget 时，从最低分起降回 l1（已 l3 的不动）。
    active = [e for e in entries if e["tier"] in ("l2", "l3")]
    if len(active) > budget:
        demotable = sorted(
            (e for e in active if not e["_locked"]), key=lambda e: e["score"]
        )
        for e in demotable[: len(active) - budget]:
            e["tier"] = "l1"
            e["reason"] = f"超出预算 {budget}，按分数降回 l1"

    return [
        {"content_id": e["content_id"], "tier": e["tier"], "score": e["score"], "reason": e["reason"]}
        for e in entries
    ]


def _latest_metric(db: Session, content_id: int) -> ContentMetricSnapshot | None:
    """取某内容最新的指标快照（与 comment_gate 同义）。"""
    return db.scalar(
        select(ContentMetricSnapshot)
        .where(ContentMetricSnapshot.content_id == content_id)
        .order_by(ContentMetricSnapshot.captured_at.desc())
        .limit(1)
    )


def _ready_asset(db: Session, content_id: int) -> MediaAsset | None:
    """返回该内容**已下载完成**的素材；未建素材/未下载完 → None。

    抽帧与视频理解都要读本地视频，故只对 `download_status == "done"` 的素材入队，
    否则 worker 只会因“素材尚未下载”反复失败重试。
    """
    return db.scalar(
        select(MediaAsset)
        .where(MediaAsset.content_id == content_id, MediaAsset.download_status == "done")
        .order_by(MediaAsset.id)
    )


def _existing_tier(asset: MediaAsset) -> str:
    return str(asset.process_tier or "")


def select_targets(
    db: Session, contents: list[ContentItem], *, settings: dict
) -> list[tuple[MediaAsset, str, str]]:
    """选出需自动加工的内容，返回 `[(asset, tier, reason), ...]`（tier ∈ {l2,l3}）。

    候选来源：传入内容中 **`content_type == "video"` 且已下载完成**的素材（抽帧/理解只对
    视频有意义）。指标取最新快照；无快照的候选不参与（`select_tier` 会把缺失记 0 分）。
    同一批按热度分位/预算决定，已 l3 的不降级由 `select_tier` 保证。
    """
    candidates: list[dict] = []
    by_id: dict[object, tuple[ContentItem, MediaAsset]] = {}
    for c in contents:
        if c.content_type != "video":
            continue
        asset = _ready_asset(db, c.id)
        if asset is None:
            continue
        m = _latest_metric(db, c.id)
        if m is None:
            continue
        candidates.append(
            {
                "content_id": c.id,
                "like": m.like,
                "comment": m.comment,
                "play": m.play,
                "existing_tier": _existing_tier(asset),
            }
        )
        by_id[c.id] = (c, asset)

    decisions = select_tier(candidates, settings)
    out: list[tuple[MediaAsset, str, str]] = []
    for d in decisions:
        if d["tier"] not in _TIER_JOB:
            continue  # l1：无需额外加工（转录可选，不在本分支自动范围）
        c, asset = by_id[d["content_id"]]
        # 已加工到位的不重复入队（幂等，避免每次同步都重跑）：
        # 档位由 run_extract_frames/run_understand 落库，已达目标档即跳过；
        # 处理中途失败时档位未升，故仍会重试（配合队列 dedup 吸收重复）。
        if _RANK.get(_existing_tier(asset), 0) >= _RANK[d["tier"]]:
            continue
        out.append((asset, d["tier"], d["reason"]))
    return out


def enqueue_process_jobs(
    db: Session, contents: list[ContentItem], *, settings: dict
) -> int:
    """按动态门控为内容自动入队分档加工任务，返回**本次新建**的 job 条数。

    与 `comment_gate.enqueue_comment_jobs` 同构：只计算、只入队，不执行加工（worker 负责）。
    调用方需保证指标快照与素材状态已落库/提交，门控才读得到。
    `enqueue(dedup=True)` 会命中已有 queued/running 同类 job，这里先记录它们，
    才能把返回值如实报成「本次新建条数」。
    """
    from app.core.queue import enqueue

    targets = select_targets(db, contents, settings=settings)
    if not targets:
        return 0

    pending: set[tuple[str, int]] = set()
    rows = db.scalars(
        select(JobQueue).where(
            JobQueue.job_type.in_(tuple(_TIER_JOB.values())),
            JobQueue.status.in_(("queued", "running")),
        )
    ).all()
    for job in rows:
        aid = (job.payload or {}).get("asset_id")
        if aid is not None:
            pending.add((job.job_type, int(aid)))

    created = 0
    for asset, tier, _reason in targets:
        job_type = _TIER_JOB[tier]
        if (job_type, asset.id) in pending:
            continue
        job = enqueue(db, job_type, {"asset_id": asset.id}, priority=_PROCESS_PRIORITY, commit=False)
        key = (job.job_type, int((job.payload or {}).get("asset_id", asset.id)))
        if key not in pending:
            pending.add(key)
            created += 1
        # 入队即置状态，前端轮询能立刻看到（与手动 API 行为一致）。
        if tier == "l2" and asset.frames_status not in ("doing", "done"):
            asset.frames_status = "pending"
        elif tier == "l3":
            asset.understand_status = "pending"
    db.commit()
    return created
