"""架构地基写入服务：creator_profile / raw_payload / task_log 三张表。

设计见 `docs/plans/2026-10-06-architecture-foundation-and-native-crawlers-design.md` Part 1。
三张表都是**增强项**：采集/同步/下载的主流程不依赖它们，落库失败也绝不能把一次
成功的任务翻转成 error。因此这里统一遵守两条纪律：

1. 每个公开函数都自带防御，**绝不向调用方抛异常**；出问题只记日志。
2. 写入放在 SAVEPOINT 内：失败只回滚该保存点，外层事务里已写入的内容（content_item 等）
   不受影响，session 也不会卡在"待回滚"状态拖垮后续 commit。

调用方负责 commit（与 crawl/sync 既有的事务边界一致）。
"""

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreatorProfile, RawPayload, TaskLog, utcnow

logger = logging.getLogger("platform.foundation")

# raw_payload 单条留存上限（序列化字节）。超过则截断存预览，避免 JSON 列被原始响应撑爆。
DEFAULT_MAX_BYTES = 64 * 1024

# 字符串列宽（与 models.py 对齐），用于写前截断，避免 PG StringDataRightTruncation。
_COL_WIDTH = {"author_name": 128, "avatar": 512, "bio": 0, "homepage_url": 512}


def upsert_creator(
    db: Session,
    platform: str,
    author_id: str,
    *,
    author_name: str = "",
    avatar: str = "",
    bio: str = "",
    follower_count: int = 0,
    following_count: int = 0,
    verified: bool = False,
    homepage_url: str = "",
    raw: dict | None = None,
) -> CreatorProfile | None:
    """按 `(platform, author_id)` upsert 创作者实体。

    - `author_id` 为空则直接返回 None（没有 upsert 的键）。
    - **只更新非空字段**：平台这次没带回的字段不会把已存的值覆盖成空。
    - 命中或新建都会刷新 `last_synced_at`。
    - 任何异常都被吞掉（记日志），失败返回 None，不影响调用方。
    """
    if not author_id:
        return None
    key = str(author_id)

    def _op() -> CreatorProfile:
        profile = db.scalar(
            select(CreatorProfile).where(
                CreatorProfile.platform == platform, CreatorProfile.author_id == key
            )
        )
        if profile is None:
            profile = CreatorProfile(platform=platform, author_id=key)
            db.add(profile)
        # 只覆盖有值的字段，避免用默认空值把已有信息冲掉
        for attr, value in (
            ("author_name", author_name),
            ("avatar", avatar),
            ("bio", bio),
            ("homepage_url", homepage_url),
        ):
            if value:
                # 按列宽截断，避免 PG 下超长触发 StringDataRightTruncation，
                # 导致整行（连同已 flush 的字段）被 savepoint 回滚而静默丢失。
                limit = _COL_WIDTH.get(attr, 0)
                setattr(profile, attr, str(value)[:limit] if limit else str(value))
        if follower_count:
            profile.follower_count = _as_int(follower_count)
        if following_count:
            profile.following_count = _as_int(following_count)
        if verified:
            profile.verified = True
        if raw:
            profile.raw = _jsonable(raw)
        profile.last_synced_at = utcnow()
        db.flush()
        return profile

    try:
        return _in_savepoint(db, _op)
    except Exception:  # noqa: BLE001 地基写入失败不得影响主流程
        rollback_if_broken(db)
        logger.exception("creator_profile 写入失败，已忽略")
        return None


def record_raw(
    db: Session,
    platform: str,
    source: str,
    external_id: str,
    payload,
    *,
    content_id: int | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> None:
    """留存一条原始 payload（可回溯、可重解析）。

    - `external_id` 为空则 no-op。
    - 序列化后超过 `max_bytes` 则截断存预览 + 原始字节数（不整条丢弃，保留可追溯性）。
    - **绝不向调用方抛异常**（payload 问题、DB 问题都吞掉记日志）。
    """
    if not external_id:
        return
    limit = max_bytes if max_bytes else DEFAULT_MAX_BYTES

    def _op() -> None:
        db.add(
            RawPayload(
                platform=platform,
                source=source,
                external_id=str(external_id)[:128],
                payload=_fit_payload(payload, limit),
                content_id=content_id,
            )
        )
        db.flush()

    try:
        _in_savepoint(db, _op)
    except Exception:  # noqa: BLE001
        rollback_if_broken(db)
        logger.exception("raw_payload 写入失败，已忽略")


def log_task(
    db: Session,
    job_id: int | None,
    job_type: str,
    level: str,
    message: str,
    *,
    detail: dict | None = None,
    actor: str = "worker",
) -> None:
    """写一条结构化任务事件（claim/完成/失败/重试）。

    保留关键日志供 pipeline 详情页与排障；**绝不向调用方抛异常**。
    """

    def _op() -> None:
        db.add(
            TaskLog(
                job_id=job_id,
                job_type=str(job_type or ""),
                level=level,
                message=str(message or "")[:512],  # 列宽 512，PG 上超长会直接报错
                detail=_jsonable(detail) if detail else {},
                actor=actor,
            )
        )
        db.flush()

    try:
        _in_savepoint(db, _op)
    except Exception:  # noqa: BLE001
        rollback_if_broken(db)
        logger.exception("task_log 写入失败，已忽略")


def rollback_if_broken(db: Session) -> None:
    """session 进入"待回滚"状态时清干净，避免后续 commit 抛 PendingRollbackError。"""
    try:
        if not db.is_active:
            db.rollback()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _in_savepoint(db: Session, op):
    """在 SAVEPOINT 内执行 op：失败只回滚该保存点，外层事务不受影响。"""
    nested = None
    try:
        nested = db.begin_nested()
    except Exception:  # noqa: BLE001 极端情况下拿不到保存点，退化为直接执行
        nested = None
    try:
        result = op()
    except Exception:
        if nested is not None:
            try:
                nested.rollback()
            except Exception:  # noqa: BLE001
                pass
        raise
    if nested is not None:
        nested.commit()
    return result


# content_item 各文本列的宽度（与 models.py 对齐）。平台返回的文案常常超长
# （抖音长视频简介可上千字），不截断会在 PG 上直接 StringDataRightTruncation。
_CONTENT_WIDTH = {
    "title": 512,
    "author_name": 128,
    "author_id": 128,
    "url": 1024,
    "cover_url": 1024,
    "external_id": 128,
}


def clamp_str(value, width: int) -> str:
    """按列宽截断字符串（None -> ""），避免超长写库报错。"""
    text = "" if value is None else str(value)
    return text[:width] if width and width > 0 else text


def normalize_content_fields(item: dict) -> dict:
    """把平台返回的内容字典里各文本字段截到列宽内；非文本键原样透传。"""
    out = dict(item)
    for key, width in _CONTENT_WIDTH.items():
        if key in out:
            out[key] = clamp_str(out[key], width)
    return out


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def follower_count_of(profile) -> int:
    """读博主粉丝数：列值优先，列为 0 时回退原始快照里的 ``follower_count``。

    历史数据里有一批行在适配器还没解析粉丝数时落库（列写 0），但 ``raw`` 快照
    一直带着真实数字。这里做读取兜底，让这些行不必等下一次刷新就能显示；
    下次刷新会按适配器的新解析把列值写正，兜底自然失效。
    """
    if profile is None:
        return 0
    count = _as_int(getattr(profile, "follower_count", 0))
    if count:
        return count
    raw = getattr(profile, "raw", None)
    if isinstance(raw, dict):
        return _as_int(raw.get("follower_count"))
    return 0


def _jsonable(value):
    """把任意对象归一为可 JSON 序列化的结构（不可序列化的值退化为字符串）。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        return {}


def _fit_payload(payload, max_bytes: int):
    """归一 payload 为可 JSON 序列化、且序列化后不超过 max_bytes 的结构。"""
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        try:
            text = json.dumps(str(payload), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = "null"
    size = len(text.encode("utf-8"))
    if size <= max_bytes:
        try:
            value = json.loads(text)
        except Exception:  # noqa: BLE001
            value = {"value": text}
        return value if isinstance(value, (dict, list)) else {"value": value}
    # 超限：留预览 + 原始字节数，并保证整行序列化后仍落在上限内
    marker = {"_truncated": True, "_original_bytes": size}
    n = max_bytes
    while n > 0:
        candidate = {**marker, "preview": text[:n]}
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= max_bytes:
            return candidate
        n = n * 3 // 4
    return marker
