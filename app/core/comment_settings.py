"""评论抓取配置：存 system_setting KV，读取时取最新值，缺失用默认。

红线：门控参数不写死在业务代码里，运营可在系统设置页调整。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SystemSetting


def _as_bool(raw) -> bool:
    """KV 里存的是字符串，不能用 bool("False")（会得到 True）。"""
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")

# system_setting key -> (默认值, 类型)
_DEFAULTS: dict[str, tuple[object, type]] = {
    "comment_gate_top_pct": (0.2, float),
    "comment_gate_budget": (10, int),
    "comment_gate_min_comments": (20, int),
    "comment_gate_min_likes": (100, int),
    "comment_gate_weight": (20, float),
    "comment_fetch_pages": (2, int),
    "comment_fetch_per_page": (20, int),
    "comment_fetch_top_n": (20, int),
    "comment_fetch_interval_sec": (1.0, float),
    "comment_fetch_sub_comments": (False, bool),
}

# 对外短名 -> system_setting key（单一事实来源：app/api/system.py 复用此映射）
COMMENT_KEYS = {
    "top_pct": "comment_gate_top_pct",
    "budget": "comment_gate_budget",
    "min_comments": "comment_gate_min_comments",
    "min_likes": "comment_gate_min_likes",
    "weight": "comment_gate_weight",
    "pages": "comment_fetch_pages",
    "per_page": "comment_fetch_per_page",
    "top_n": "comment_fetch_top_n",
    "interval": "comment_fetch_interval_sec",
    "with_sub_comments": "comment_fetch_sub_comments",
}


def get_comment_settings(db: Session) -> dict:
    rows = {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}
    out: dict = {}
    for short, key in COMMENT_KEYS.items():
        default, typ = _DEFAULTS[key]
        raw = rows.get(key)
        if raw is None or raw == "":
            out[short] = typ(default)  # 默认值也按声明类型归一（如 weight 20 -> 20.0）
            continue
        try:
            if typ is bool:
                out[short] = _as_bool(raw)
                continue
            out[short] = typ(raw)
        except (TypeError, ValueError):
            out[short] = typ(default)
    return out
