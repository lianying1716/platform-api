"""AI 任务运行时段：用户可限制调用大模型的任务何时开始执行。

配置存在 ``system_setting.ai_run_schedule``（JSON），读取时取最新值。时段按自然日
的本地时间判断，支持跨午夜（例如 23:00-07:00）和按星期筛选。窗口左闭右开：
``09:00-18:00`` 表示 09:00 开始、18:00 整不再开始新任务。

这里只负责“现在能不能开始一个 AI 任务”，不负责中断已经跑起来的任务。队列在
领取任务时调用它，窗口外的任务保留 queued，等下一个窗口自动继续。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SystemSetting

SETTING_KEY = "ai_run_schedule"
DEFAULT_TIMEZONE = "Asia/Shanghai"

# 真正会调用大模型、属于“任务加工”的队列类型。模型接入页的连通性检测和探针
# 是用户主动发起的运维操作，不受这里的时段限制。
AI_JOB_TYPES = frozenset({"analyze", "understand_video", "breakdown"})
AI_JOB_LABELS = {
    "analyze": "AI 速览",
    "understand_video": "视频理解（含判类）",
    "breakdown": "爆款拆解",
}

_ALL_DAYS = list(range(7))  # 周一 0 ... 周日 6
_DEFAULT_WINDOWS = [{"days": list(_ALL_DAYS), "start": "09:00", "end": "22:00"}]
_WEEKDAYS = "一二三四五六日"


class AiScheduleError(ValueError):
    """配置结构或时间值不合法。"""


def _parse_hhmm(value: object) -> tuple[str, int]:
    if not isinstance(value, str):
        raise AiScheduleError("时间必须是 HH:MM 字符串")
    text = value.strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise AiScheduleError(f"时间格式应为 HH:MM，收到 {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise AiScheduleError(f"时间超出范围：{value!r}")
    return f"{hour:02d}:{minute:02d}", hour * 60 + minute


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # Windows/Python 精简镜像可能没有 tzdata；中国标准时间没有夏令时，
        # 固定 +08:00 与 Asia/Shanghai 在该场景等价。
        if name in ("Asia/Shanghai", "Asia/Chongqing", "PRC"):
            return timezone(timedelta(hours=8), name)
        raise AiScheduleError(f"未知时区：{name}") from None


def normalize_ai_schedule(data: object) -> dict:
    """校验并归一化配置；非法值抛 ``AiScheduleError``（API 层转 422）。"""
    if not isinstance(data, dict):
        raise AiScheduleError("配置必须是对象")

    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise AiScheduleError("enabled 必须是布尔值")

    timezone_name = data.get("timezone", DEFAULT_TIMEZONE)
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise AiScheduleError("timezone 必须是非空字符串")
    timezone_name = timezone_name.strip()
    _zone(timezone_name)

    raw_windows = data.get("windows", [])
    if not isinstance(raw_windows, list):
        raise AiScheduleError("windows 必须是数组")
    if len(raw_windows) > 24:
        raise AiScheduleError("时段最多 24 个")
    if enabled and not raw_windows:
        raise AiScheduleError("开启时段限制时至少要有一个时段")

    windows: list[dict] = []
    for idx, item in enumerate(raw_windows, start=1):
        if not isinstance(item, dict):
            raise AiScheduleError(f"第 {idx} 个时段必须是对象")
        days = item.get("days", _ALL_DAYS)
        if not isinstance(days, list) or not days:
            raise AiScheduleError(f"第 {idx} 个时段至少要选择一天")
        if any(isinstance(d, bool) or not isinstance(d, int) or d not in _ALL_DAYS for d in days):
            raise AiScheduleError(f"第 {idx} 个时段的星期取值必须是 0-6")
        start, start_min = _parse_hhmm(item.get("start"))
        end, end_min = _parse_hhmm(item.get("end"))
        if start_min == end_min:
            raise AiScheduleError(f"第 {idx} 个时段的开始和结束不能相同")
        windows.append({"days": sorted(set(days)), "start": start, "end": end})

    return {"enabled": enabled, "timezone": timezone_name, "windows": windows}


def default_ai_schedule() -> dict:
    return {"enabled": False, "timezone": DEFAULT_TIMEZONE, "windows": _copy_windows(_DEFAULT_WINDOWS)}


def _copy_windows(windows: list[dict]) -> list[dict]:
    return [{"days": list(w["days"]), "start": w["start"], "end": w["end"]} for w in windows]


def resolve_ai_schedule(raw: str | None) -> dict:
    """从 KV 字符串解析；缺失或损坏时回到“不限制”的默认配置。"""
    if raw is None or not str(raw).strip():
        return default_ai_schedule()
    try:
        return normalize_ai_schedule(json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default_ai_schedule()


def get_ai_schedule(db: Session) -> dict:
    row = db.get(SystemSetting, SETTING_KEY)
    return resolve_ai_schedule(row.value if row is not None else None)


def _clock_minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _window_contains(local: datetime, window: dict) -> bool:
    days = window["days"]
    minute = local.hour * 60 + local.minute
    start = _clock_minutes(window["start"])
    end = _clock_minutes(window["end"])
    weekday = local.weekday()
    if start < end:
        return weekday in days and start <= minute < end
    # 跨午夜：前一段属于当天，后一段属于次日（仍在「开始日」选中的星期内）。
    yesterday = (weekday - 1) % 7
    return (weekday in days and minute >= start) or (yesterday in days and minute < end)


def _now_utc() -> datetime:
    """测试注入点：返回带 UTC 时区的当前时间。"""
    return datetime.now(timezone.utc)


def is_ai_allowed(schedule: dict, *, now: datetime | None = None) -> bool:
    """当前是否允许开始新的 AI 任务；关闭限制时永远为 True。"""
    if not schedule.get("enabled"):
        return True
    zone = _zone(str(schedule.get("timezone") or DEFAULT_TIMEZONE))
    current = (now or _now_utc()).astimezone(zone)
    return any(_window_contains(current, w) for w in schedule.get("windows", []))


def next_ai_open_at(schedule: dict, *, now: datetime | None = None) -> datetime | None:
    """返回下一次允许开始 AI 任务的时间（带时区）；未启用限制返回 None。"""
    if not schedule.get("enabled"):
        return None
    zone = _zone(str(schedule.get("timezone") or DEFAULT_TIMEZONE))
    current = (now or _now_utc()).astimezone(zone)
    if is_ai_allowed(schedule, now=current):
        return current

    # 时段只精确到分钟；最多向后找 8 天，足以覆盖「每周某几天」的配置。
    candidate = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(8 * 24 * 60):
        if any(_window_contains(candidate, w) for w in schedule.get("windows", [])):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def ai_schedule_status(schedule: dict, *, now: datetime | None = None) -> dict:
    opened = next_ai_open_at(schedule, now=now)
    return {
        **schedule,
        "summary": describe_windows(schedule),
        "is_open": is_ai_allowed(schedule, now=now),
        "next_open_at": opened.isoformat() if opened is not None else None,
        # 额外给前端一份“按配置时区”的短文本，避免浏览器本地时区把 +08:00 转成 UTC。
        "next_open_local": opened.strftime("%m-%d %H:%M") if opened is not None else None,
        "controlled_jobs": [
            {"job_type": job_type, "label": AI_JOB_LABELS[job_type]}
            for job_type in sorted(AI_JOB_TYPES)
        ],
    }


def describe_windows(schedule: dict) -> list[str]:
    """把时段转成人能扫一眼的短句，例如「周一至周五 09:00-18:00」。"""
    labels: list[str] = []
    for window in schedule.get("windows", []):
        days = sorted(set(window.get("days", [])))
        if days == _ALL_DAYS:
            day_text = "每天"
        elif len(days) > 1 and days == list(range(days[0], days[-1] + 1)):
            day_text = f"周{_WEEKDAYS[days[0]]}至周{_WEEKDAYS[days[-1]]}"
        else:
            day_text = "、".join(f"周{_WEEKDAYS[d]}" for d in days)
        labels.append(f"{day_text} {window.get('start')}-{window.get('end')}")
    return labels
