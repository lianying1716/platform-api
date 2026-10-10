"""抓取缓存 + 风控冷却：避免频繁拉取平台数据（默认生效）。

- ``fetch_cache`` 表按 key 存 JSON + 过期时间；命中即返回、零网络请求。
- ``cooldown:*`` 键记录风控冷却：冷却期内调用方应只读缓存并发明确提示，**不硬重试**。
- ``single_flight`` 把同 key 的并发请求合并为一次执行（防用户连点打爆平台）。

TTL 集中在此，便于统一调整（越"重"的接口 TTL 越长）。
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import FetchCache

logger = logging.getLogger("platform.fetch_cache")

# 缓存 TTL（秒）
TTL_FOLLOWINGS = 6 * 3600  # 关注列表：6 小时
TTL_STATS = 24 * 3600  # 粉丝/获赞：24 小时
TTL_CREATOR_VIDEOS = 15 * 60  # 博主投稿/视频：15 分钟

# 命中风控后的冷却（秒）——期间不发起同操作请求。
# 保守档：命中风控说明平台已经警觉，冷 2 小时比 30 分钟更稳妥（宁可慢，不冒封号风险）。
COOLDOWN_SECONDS = 2 * 3600

# 进程内 single-flight 锁（key -> Lock）
_locks: dict[str, "asyncio.Lock"] = {}


def get_cache(db: Session, key: str) -> dict | None:
    """取缓存；不存在或已过期返回 None（过期的顺手删除）。"""
    row = db.get(FetchCache, key)
    if row is None:
        return None
    if row.expires_at <= datetime.utcnow():
        db.delete(row)
        db.flush()
        return None
    return row.value


def set_cache(db: Session, key: str, value: dict, ttl_seconds: int) -> None:
    """写入/覆盖缓存。"""
    payload = value if isinstance(value, dict) else {"value": value}
    expires_at = datetime.utcnow() + timedelta(seconds=max(1, ttl_seconds))
    row = db.get(FetchCache, key)
    if row is None:
        db.add(FetchCache(key=key, value=payload, expires_at=expires_at))
    else:
        row.value = payload
        row.expires_at = expires_at
    db.flush()


def purge_expired(db: Session) -> int:
    """删除所有过期行，返回删除条数（调度器周期调用）。"""
    result = db.execute(delete(FetchCache).where(FetchCache.expires_at <= datetime.utcnow()))
    db.flush()
    return int(result.rowcount or 0)


# ---------------- 风控冷却 ----------------


def set_cooldown(db: Session, key: str, seconds: int = COOLDOWN_SECONDS) -> None:
    """记录风控冷却（读操作命中风控后调用）。"""
    set_cache(db, key, {"cooldown": True, "since": datetime.utcnow().isoformat()}, ttl_seconds=seconds)


def is_cooling(db: Session, key: str) -> bool:
    return get_cache(db, key) is not None


def cooldown_remaining(db: Session, key: str) -> int:
    """剩余冷却秒数；不在冷却返回 0。"""
    row = db.get(FetchCache, key)
    if row is None:
        return 0
    remain = int((row.expires_at - datetime.utcnow()).total_seconds())
    return max(0, remain)


# 风控识别：平台在不同场景返回的标记
_RISK_MARKERS = (
    "风控",
    "-352",
    "-412",
    "412",
    "403",
    "too many requests",
    "argussecurityplugin",
    "blocked",
    "captcha",
    "verify",
)


def looks_like_risk(text: str) -> bool:
    """粗略判断一段响应/异常文本是否为风控（用于决定是否进入冷却）。"""
    if not text:
        return False
    low = str(text).lower()
    return any(marker in low for marker in _RISK_MARKERS)


# ---------------- 并发合并 (single-flight) ----------------

# key -> 正在进行的 asyncio.Task；并发调用同一 key 时复用同一 task
_inflight: dict[str, "asyncio.Task"] = {}


async def single_flight(key: str, coro_factory):
    """同 key 并发时只执行一次 ``coro_factory()``，其余调用等待并复用同一结果。

    与单纯加锁不同：后来者直接 await 先到者的 task，因此底层只执行一次
    （防用户连点打爆平台）。
    """
    existing = _inflight.get(key)
    if existing is not None:
        return await asyncio.shield(existing)
    task = asyncio.ensure_future(coro_factory())
    _inflight[key] = task
    try:
        return await asyncio.shield(task)
    finally:
        _inflight.pop(key, None)
