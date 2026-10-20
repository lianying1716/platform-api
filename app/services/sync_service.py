"""订阅同步服务：把某账号下的收藏夹/关注博主/合集的新内容拉进 content_item，
并按 auto_download 决定是否给 media_asset 建下载任务。

真实的"列远端源"和"拉取内容列表"由各平台适配器提供（见 modules/*.py 的
list_sources / fetch_source）。这里只做编排：取凭证 -> 调适配器 -> 去重入库 ->
建素材与下载任务 -> 写 sync_run。

注意：真实平台数据需要用户已登录的有效 Cookie 才能拉到。没有凭证或凭证失效时，
sync_run 记 error，不影响平台其他部分。
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import events
from app.core.crypto import decrypt_credential
from app.core.queue import enqueue
from app.models import (
    ContentItem,
    ContentMetricSnapshot,
    ContentSource,
    MediaAsset,
    PlatformAccount,
    Subscription,
    SyncRun,
)
from app.modules import get_adapter
from app.services import foundation
from app.domain.metrics import normalize_metrics

# 单次订阅同步的拉取上限（保守频率，避免触发平台风控）
_SYNC_FETCH_LIMIT = 50


async def run_subscription_sync(
    db: Session,
    subscription: Subscription,
    *,
    force: bool = False,
    job_id: int | None = None,
) -> SyncRun:
    """执行一次订阅同步，返回 SyncRun。"""
    now = datetime.utcnow()
    run = SyncRun(
        subscription_id=subscription.id,
        job_id=job_id,
        status="running",
        started_at=now,
        heartbeat_at=now,
    )
    db.add(run)
    # 无论成败都记一次尝试时间：调度器据此退避，避免失败订阅被反复入队打平台
    # （风控期尤其重要）。
    subscription.last_attempt_at = now
    db.commit()
    db.refresh(run)

    if subscription.deleted_at is not None:
        return _finish(db, run, "error", error="订阅已删除，拒绝同步")
    # 关注列表默认同步的是「关注关系」；拉取博主名下视频是二级手动开关（sync_videos）。
    # 未开启时拒绝拉取，避免"看着开着、其实没同步"的假象。
    if subscription.source_type == "creator" and not subscription.sync_videos:
        return _finish(db, run, "error", error="未开启「同步博主视频」（该开关需手动打开）")
    account = db.get(PlatformAccount, subscription.account_id)
    if account is None or account.credential is None:
        return _finish(db, run, "error", error="账号不存在或未录入凭证")
    if account.status != "active":
        return _finish(db, run, "error", error=f"账号已停用或不可用（status={account.status}）")
    if not bool(account.sync_enabled):
        from app.services.account_state import SILENT_MESSAGE

        return _finish(db, run, "error", error=SILENT_MESSAGE)

    adapter = get_adapter(subscription.platform)
    try:
        from app.domain.capabilities import require_source_type
        from app.domain.download import DownloadPolicy

        require_source_type(subscription.platform, subscription.source_type)
        DownloadPolicy(subscription.download_quality).validate_for(subscription.platform)
    except ValueError as exc:
        return _finish(db, run, "error", error=str(exc))
    fetch = getattr(adapter, "fetch_source", None)
    if fetch is None:
        return _finish(db, run, "error", error=f"{adapter.label} 暂不支持订阅同步")

    # 本次同步的条数上限：从设置页配置的随机区间里取一个（照抄抖小云 BatchCount 的
    # 「每次查询数量」语义），并受 _SYNC_FETCH_LIMIT 安全上限约束。
    from app.core.sync_settings import get_sync_settings, pick_batch_size

    batch = pick_batch_size(get_sync_settings(db))
    fetch_limit = min(batch, _SYNC_FETCH_LIMIT)
    try:
        credential = decrypt_credential(account.credential.ciphertext)
        items = await _fetch_source_cached(db, subscription, fetch, credential, fetch_limit, force=force)
    except Exception as exc:  # noqa: BLE001 网络/风控异常不应让平台崩
        return _finish(db, run, "error", error=f"拉取失败: {exc}")

    fetched_count = len(items)
    items = _apply_sync_settings(items, subscription)

    added = 0
    updated = 0
    touched: list[ContentItem] = []
    for item in items:
        content, is_new = _upsert_content(db, subscription, item)
        _persist_foundation(db, subscription.platform, "subscription", content, item)
        _record_metric(db, content, item)
        touched.append(content)
        if is_new:
            added += 1
            if subscription.auto_download and content.content_type == "video":
                _ensure_download(db, content, subscription.download_quality)
        elif not subscription.only_new:
            # 全量同步会覆盖刷新已有内容：必须能观测到"确实刷新了几条"。
            updated += 1
    subscription.last_sync_at = datetime.utcnow()
    events.publish("sync_done", {"subscription_id": subscription.id, "added": added}, db=db)
    db.commit()
    # 评论富集：动态门控后入队（不阻塞，worker 抓）。指标快照已在上方 commit，门控才读得到。
    from app.core.comment_settings import get_comment_settings
    from app.services.comment_gate import enqueue_comment_jobs

    try:
        enqueue_comment_jobs(
            db,
            touched,
            platform=subscription.platform,
            settings=get_comment_settings(db),
            mode=subscription.comment_mode,
        )
    except Exception:  # noqa: BLE001 评论富集是增强项，失败不得翻转本次同步的成功状态
        db.rollback()
        logging.getLogger("platform.sync").exception("评论入队失败，已忽略（不影响本次同步）")
    # 动态分档加工：热度门控决定是否升到高级档后自动入队（设计 §6.1）。只对**已下载完成**的素材入队，
    # 新同步内容的视频尚未下载，将在下载完成后由 handle_download 再走一次门控。
    from app.core.process_settings import get_process_settings
    from app.services.process_gate import enqueue_process_jobs

    try:
        enqueue_process_jobs(db, touched, settings=get_process_settings(db))
    except Exception:  # noqa: BLE001 分档加工是增强项，失败不得翻转本次同步的成功状态
        db.rollback()
        logging.getLogger("platform.sync").exception("分档入队失败，已忽略（不影响本次同步）")
    return _finish(db, run, "success", fetched=fetched_count, added=added, updated=updated)


async def _fetch_source_cached(
    db: Session, sub: Subscription, fetch, credential, limit: int, *, force: bool = False
) -> list[dict]:
    """带缓存 + 风控冷却的抓取（仅订阅同步路径用）。

    - 15 分钟 TTL：频繁的手动同步不会重复打平台。
    - 风控冷却：命中风控后冷却期内直接读缓存（无缓存则抛可读异常，不硬重试）。

    只对 **creator 订阅**启用（博主投稿要签名/易风控、且是「关注博主」页高频触发路径）；
    favorite/collection 保持直取，避免把「内容变化」也缓存住。
    """
    from app.core import fetch_cache

    if sub.source_type != "creator":
        return await fetch(credential, sub.source_type, sub.remote_id, limit)

    cache_key = f"creator_videos:{sub.platform}:{sub.account_id}:{sub.remote_id}"
    cooldown_key = f"cooldown:{sub.platform}:{sub.account_id}:videos"
    cached = None if force else fetch_cache.get_cache(db, cache_key)
    if cached is not None:
        return cached.get("items") or []
    if fetch_cache.is_cooling(db, cooldown_key):
        remain = fetch_cache.cooldown_remaining(db, cooldown_key)
        raise RuntimeError(f"平台风控冷却中，约 {max(1, remain // 60)} 分钟后重试")

    async def _do():
        return await fetch(credential, sub.source_type, sub.remote_id, limit)

    try:
        items = await fetch_cache.single_flight(cache_key, _do)
    except Exception as exc:  # noqa: BLE001
        if fetch_cache.looks_like_risk(str(exc)):
            fetch_cache.set_cooldown(db, cooldown_key)
            db.commit()
        raise
    fetch_cache.set_cache(
        db, cache_key, {"items": items}, ttl_seconds=fetch_cache.TTL_CREATOR_VIDEOS
    )
    db.commit()
    return items


def _apply_sync_settings(items: list[dict], sub: Subscription) -> list[dict]:
    """按订阅设置过滤 + 截断本次拉取结果（确定性逻辑，便于测试）。

    - ``min_play``：有指标且播放量低于门槛的丢弃（无指标的条目保留）。
    - ``publish_after_days``：>0 且条目带可解析发布时间时，丢弃早于 N 天前的条目。
    - ``max_items``：截断到 1..500（越界值按边界钳制，防脏数据绕过上限）。
    """
    min_play = sub.min_play or 0
    if min_play > 0:
        items = [
            it
            for it in items
            if not (it.get("metrics") and (it["metrics"].get("play") is not None) and it["metrics"]["play"] < min_play)
        ]
    days = sub.publish_after_days or 0
    if days > 0:
        cutoff = datetime.utcnow() - timedelta(days=days)
        kept = []
        for it in items:
            published = _parse_published_at(it.get("published_at"))
            if published is not None and published < cutoff:
                continue
            kept.append(it)
        items = kept
    limit = min(max(sub.max_items or 0, 1), 500)
    return items[:limit]


def _parse_published_at(value) -> datetime | None:
    """把 ISO/date 发布时间解析为 naive UTC；无法解析返回 None（tz-safe）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = None
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# only_new=False 时允许被本次拉取值刷新的可变字段。
_REFRESH_FIELDS = ("title", "url", "cover_url", "author_name")


def _upsert_content(db: Session, sub: Subscription, item: dict) -> tuple[ContentItem, bool]:
    # 平台返回的文案可能超长（抖音长简介上千字），先按列宽截断再入库，
    # 否则 PG 直接 StringDataRightTruncation。
    item = foundation.normalize_content_fields(item)
    existing = db.scalar(
        select(ContentItem).where(
            ContentItem.platform == sub.platform,
            ContentItem.external_id == item["external_id"],
            ContentItem.account_id == sub.account_id,
        )
    )
    if existing:
        _touch_source(db, existing.id, sub)
        if not sub.only_new:
            # 覆盖更新：用本次拉到的值刷新可变字段（空值不覆盖，避免拉取缺字段时清空）。
            for field in _REFRESH_FIELDS:
                value = item.get(field)
                if value:
                    setattr(existing, field, value)
        return existing, False
    content = ContentItem(
        account_id=sub.account_id,
        platform=sub.platform,
        external_id=item["external_id"],
        content_type=item.get("content_type", "video"),
        title=item.get("title", ""),
        author_name=item.get("author_name", ""),
        author_id=item.get("author_id", ""),
        url=item.get("url", ""),
        cover_url=item.get("cover_url", ""),
        published_at=_parse_published_at(item.get("published_at")),
        source_kind="subscription",
        source_id=sub.id,
        extra=item.get("extra", {}),
    )
    db.add(content)
    db.flush()
    _touch_source(db, content.id, sub)
    return content, True


def _touch_source(db: Session, content_id: int, sub: Subscription) -> None:
    """同一账号内记录内容的所有订阅来源；取消订阅只停用来源，不删素材。"""
    source = db.scalar(
        select(ContentSource).where(
            ContentSource.content_id == content_id,
            ContentSource.subscription_id == sub.id,
        )
    )
    if source is None:
        source = ContentSource(
            content_id=content_id,
            account_id=sub.account_id,
            subscription_id=sub.id,
            link_kind="subscription",
            source_ref="",
            active=True,
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        db.add(source)
    else:
        source.active = True
        source.last_seen_at = datetime.utcnow()


def _persist_foundation(
    db: Session, platform: str, source: str, content: ContentItem, item: dict
) -> None:
    """地基写入（creator_profile + raw_payload）。

    增强项：失败只记日志，**绝不翻转本次同步的成功状态**。隔离靠 foundation 内部
    的 SAVEPOINT + 这里兜底的 try/except（防 helper 自身异常）。
    """
    try:
        foundation.upsert_creator(
            db,
            platform,
            item.get("author_id", ""),
            author_name=item.get("author_name", ""),
            avatar=item.get("avatar", ""),
            bio=item.get("bio", ""),
            follower_count=item.get("follower_count", 0),
            following_count=item.get("following_count", 0),
            verified=item.get("verified", False),
            homepage_url=item.get("homepage_url", ""),
            # 只认 author_raw（作者级快照）。绝不用 item["extra"] 兜底：那是**内容级**
            # 附加数据（如抖音播放地址、小红书 xsec_token），写进 creator_profile.raw
            # 会把真实作者资料（含粉丝数）覆盖成下载链接。
            raw=item.get("author_raw") or {},
        )
        foundation.record_raw(
            db, platform, source, item.get("external_id", ""), item, content_id=content.id
        )
    except Exception:  # noqa: BLE001 地基写入是增强项，失败不得翻转本次同步的成功状态
        foundation.rollback_if_broken(db)
        logging.getLogger("platform.sync").exception("地基写入失败，已忽略（不影响本次同步）")


def _record_metric(db: Session, content: ContentItem, item: dict) -> None:
    m = item.get("metrics")
    if not m:
        return
    values = normalize_metrics(m)
    db.add(
        ContentMetricSnapshot(
            content_id=content.id,
            **values,
        )
    )


def _ensure_download(db: Session, content: ContentItem, quality: str = "best") -> None:
    asset = db.scalar(select(MediaAsset).where(MediaAsset.content_id == content.id))
    if asset is None:
        asset = MediaAsset(content_id=content.id, download_status="pending")
        db.add(asset)
        db.flush()
    enqueue(
        db,
        "download",
        {"asset_id": asset.id, "url": content.url, "quality": quality},
        commit=False,
    )


def _finish(
    db: Session,
    run: SyncRun,
    status: str,
    fetched: int = 0,
    added: int = 0,
    error: str = "",
    updated: int = 0,
) -> SyncRun:
    now = datetime.utcnow()
    run.status = status
    run.fetched = fetched
    run.added = added
    run.updated = updated
    run.error = error[:500]
    run.finished_at = now
    run.heartbeat_at = None
    db.commit()
    db.refresh(run)
    return run
