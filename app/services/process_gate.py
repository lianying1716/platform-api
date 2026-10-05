"""素材加工分档门控：从一批作品里决定各自加工档位（l1 转录 / l2 抽帧 / l3 视频理解）。

动态优先、绝不写死（沿用 `comment_gate.py` 范式，阈值来自 `process_settings`）：
  score = w_like*like + w_comment*comment + w_play*play   # 热度分
  下限：like < min_likes_floor → 不高于 l1                  # 绝对不够格
  档位：score >= l3_min_heat → l3；>= l2_min_heat → l2；否则 l1
  分位：按 score 降序，只有前 top_pct 份额可高于 l1          # 随批自适应
  预算：l2/l3 合计受 budget 约束，超出的从最低分起降回 l1
  不降级：已 l3 的候选保持 l3，分数/分位/预算都不降

纯函数：不碰 DB、不发请求；调用方负责取候选与配置（`get_process_settings`）。
"""

from __future__ import annotations

# 档位从低到高；"" 表示尚未加工。
_RANK = {"": 0, "l1": 1, "l2": 2, "l3": 3}


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
