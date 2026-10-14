"""同步频率 / 批量配置（存 `system_setting` KV，照抄抖小云的可调参数）。

红线（同 `process_settings.py` / `comment_settings.py`）：这些参数**不写死在业务代码里**，
有内置默认、可在线改、读取时取最新值。抖小云对应项：
- ``BatchCount``（默认 18、上限 30）：单次同步/下载的条数上限；
- 翻页 ``Task.Delay(random.Next(2,10)*1000)``：相邻请求之间随机等待，避开固定节奏被风控。

用户要的是「随机区间」而不是单值，所以这里都成对提供 min/max：
- ``interval_min/max_minutes``：订阅到期的随机间隔（替代固定 interval_minutes 的抖动）；
- ``batch_min/max``：每次同步拉取/下载的条数范围（在区间内随机取一个）；
- ``download_gap_min/max_seconds``：相邻两次下载之间的随机等待区间。

读取侧只做类型归一与区间纠正（min>max 时交换、非法回退默认），写入侧校验放 API 层。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SystemSetting

_INT = "int"
_FLOAT = "float"

# 对外短名 -> system_setting key（单一事实来源；API / 服务复用此映射）
SYNC_KEYS: dict[str, str] = {
    "interval_min_minutes": "sync_interval_min_minutes",
    "interval_max_minutes": "sync_interval_max_minutes",
    "batch_min": "sync_batch_min",
    "batch_max": "sync_batch_max",
    "download_gap_min_seconds": "sync_download_gap_min_seconds",
    "download_gap_max_seconds": "sync_download_gap_max_seconds",
}

# system_setting key -> (默认值, 类型)。默认值贴近抖小云（BatchCount 18、翻页 2~10s）。
_DEFAULTS: dict[str, tuple[object, str]] = {
    # 订阅同步间隔随机范围（分钟）。默认 5~6 小时：比"固定整点"更像人、也错开多账号。
    "sync_interval_min_minutes": (300, _INT),
    "sync_interval_max_minutes": (360, _INT),
    # 每次同步的条数范围（抖小云 BatchCount 默认 18；上限 30 在 API 层钳制）。
    "sync_batch_min": (10, _INT),
    "sync_batch_max": (18, _INT),
    # 相邻下载的随机等待区间（秒），照抄抖小云 random(2,10)。
    "sync_download_gap_min_seconds": (2.0, _FLOAT),
    "sync_download_gap_max_seconds": (10.0, _FLOAT),
}

# 绝对上限/下限（API 写入与读取兜底共用，防脏数据把节奏调到极端）
INTERVAL_MINUTES_FLOOR = 1
INTERVAL_MINUTES_CEIL = 7 * 24 * 60  # 一周
BATCH_FLOOR = 1
BATCH_CEIL = 30  # 对齐抖小云 BatchCount 上限
DOWNLOAD_GAP_CEIL = 600.0


def _coerce(raw: str | None, default: object, kind: str) -> object:
    if raw is None or raw == "":
        return default
    try:
        if kind == _INT:
            return int(str(raw).strip())
        if kind == _FLOAT:
            return float(str(raw).strip())
        return str(raw)
    except (TypeError, ValueError):
        return default


def _clamp(value, lo, hi, kind):
    if kind == _INT:
        return int(max(lo, min(hi, value)))
    return float(max(lo, min(hi, value)))


def resolve_sync_settings_from_rows(rows: dict[str, str]) -> dict:
    """从 system_setting 行映射解析同步频率配置（不依赖 DB Session）。

    返回短名 -> 值，且保证每个区间满足 min<=max（min>max 时交换），避免脏 KV 导致
    ``random.uniform(a, b)`` 反序或调度判定异常。
    """
    out: dict = {}
    for short, key in SYNC_KEYS.items():
        default, kind = _DEFAULTS[key]
        out[short] = _coerce(rows.get(key), default, kind)

    # 区间纠正 + 边界钳制
    out["interval_min_minutes"] = _clamp(out["interval_min_minutes"], INTERVAL_MINUTES_FLOOR, INTERVAL_MINUTES_CEIL, _INT)
    out["interval_max_minutes"] = _clamp(out["interval_max_minutes"], INTERVAL_MINUTES_FLOOR, INTERVAL_MINUTES_CEIL, _INT)
    if out["interval_min_minutes"] > out["interval_max_minutes"]:
        out["interval_min_minutes"], out["interval_max_minutes"] = (
            out["interval_max_minutes"], out["interval_min_minutes"],
        )

    out["batch_min"] = _clamp(out["batch_min"], BATCH_FLOOR, BATCH_CEIL, _INT)
    out["batch_max"] = _clamp(out["batch_max"], BATCH_FLOOR, BATCH_CEIL, _INT)
    if out["batch_min"] > out["batch_max"]:
        out["batch_min"], out["batch_max"] = out["batch_max"], out["batch_min"]

    out["download_gap_min_seconds"] = _clamp(out["download_gap_min_seconds"], 0.0, DOWNLOAD_GAP_CEIL, _FLOAT)
    out["download_gap_max_seconds"] = _clamp(out["download_gap_max_seconds"], 0.0, DOWNLOAD_GAP_CEIL, _FLOAT)
    if out["download_gap_min_seconds"] > out["download_gap_max_seconds"]:
        out["download_gap_min_seconds"], out["download_gap_max_seconds"] = (
            out["download_gap_max_seconds"], out["download_gap_min_seconds"],
        )
    return out


def get_sync_settings(db: Session) -> dict:
    rows = {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}
    return resolve_sync_settings_from_rows(rows)


def pick_interval_minutes(settings_map: dict) -> int:
    """在配置的随机区间里取一个间隔（分钟）。"""
    import random

    lo = int(settings_map.get("interval_min_minutes", 300))
    hi = int(settings_map.get("interval_max_minutes", 360))
    return random.randint(lo, max(lo, hi))


def pick_batch_size(settings_map: dict) -> int:
    """在配置的随机区间里取一次同步的条数上限。"""
    import random

    lo = int(settings_map.get("batch_min", 10))
    hi = int(settings_map.get("batch_max", 18))
    return random.randint(lo, max(lo, hi))


def pick_download_gap_seconds(settings_map: dict) -> float:
    """在配置的随机区间里取相邻下载的等待秒数。"""
    import random

    lo = float(settings_map.get("download_gap_min_seconds", 2.0))
    hi = float(settings_map.get("download_gap_max_seconds", 10.0))
    return random.uniform(lo, max(lo, hi))
