"""订阅同步服务：把某账号下的收藏夹/关注博主/合集的新内容拉进 content_item，
并按 auto_download 决定是否给 media_asset 建下载任务。

真实的"列远端源"和"拉取内容列表"由各平台适配器提供（见 modules/*.py 的
list_sources / fetch_source）。这里只做编排：取凭证 -> 调适配器 -> 去重入库 ->
建素材与下载任务 -> 写 sync_run。

注意：真实平台数据需要用户已登录的有效 Cookie 才能拉到。没有凭证或凭证失效时，
sync_run 记 error，不影响平台其他部分。
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import events
from app.core.crypto import decrypt_credential
from app.core.queue import enqueue
from app.models import (
    ContentItem,
    ContentMetricSnapshot,
    MediaAsset,
    PlatformAccount,
    Subscription,
    SyncRun,
)
from app.modules import get_adapter

# 单次订阅同步的拉取上限（保守频率，避免触发平台风控）
_SYNC_FETCH_LIMIT = 50


async def run_subscription_sync(db: Session, subscription: Subscription) -> SyncRun:
    """执行一次订阅同步，返回 SyncRun。"""
    run = SyncRun(subscription_id=subscription.id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    account = db.get(PlatformAccount, subscription.account_id)
    if account is None or account.credential is None:
        return _finish(db, run, "error", error="账号不存在或未录入凭证")

    adapter = get_adapter(subscription.platform)
    fetch = getattr(adapter, "fetch_source", None)
    if fetch is None:
        return _finish(db, run, "error", error=f"{adapter.label} 暂不支持订阅同步")

    try:
        credential = decrypt_credential(account.credential.ciphertext)
        items = await fetch(credential, subscription.source_type, subscription.remote_id, _SYNC_FETCH_LIMIT)
    except Exception as exc:  # noqa: BLE001 网络/风控异常不应让平台崩
        return _finish(db, run, "error", error=f"拉取失败: {exc}")

    added = 0
    touched: list[ContentItem] = []
    for item in items:
        content, is_new = _upsert_content(db, subscription, item)
        _record_metric(db, content, item)
        touched.append(content)
        if is_new:
            added += 1
            if subscription.auto_download and content.content_type == "video":
                _ensure_download(db, content)
    subscription.last_sync_at = datetime.utcnow()
    db.commit()
    events.publish("sync_done", {"subscription_id": subscription.id, "added": added})
    # 评论富集：动态门控后入队（不阻塞，worker 抓）。指标快照已在上方 commit，门控才读得到。
    from app.core.comment_settings import get_comment_settings
    from app.services.comment_gate import enqueue_comment_jobs

    try:
        enqueue_comment_jobs(db, touched, platform=subscription.platform, settings=get_comment_settings(db))
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
    return _finish(db, run, "success", fetched=len(items), added=added)


def _upsert_content(db: Session, sub: Subscription, item: dict) -> tuple[ContentItem, bool]:
    existing = db.scalar(
        select(ContentItem).where(
            ContentItem.platform == sub.platform, ContentItem.external_id == item["external_id"]
        )
    )
    if existing:
        return existing, False
    content = ContentItem(
        platform=sub.platform,
        external_id=item["external_id"],
        content_type=item.get("content_type", "video"),
        title=item.get("title", ""),
        author_name=item.get("author_name", ""),
        author_id=item.get("author_id", ""),
        url=item.get("url", ""),
        cover_url=item.get("cover_url", ""),
        published_at=item.get("published_at"),
        source_kind="subscription",
        source_id=sub.id,
        extra=item.get("extra", {}),
    )
    db.add(content)
    db.flush()
    return content, True


def _record_metric(db: Session, content: ContentItem, item: dict) -> None:
    m = item.get("metrics")
    if not m:
        return
    db.add(
        ContentMetricSnapshot(
            content_id=content.id,
            play=m.get("play", 0),
            like=m.get("like", 0),
            comment=m.get("comment", 0),
            favorite=m.get("favorite", 0),
            share=m.get("share", 0),
        )
    )


def _ensure_download(db: Session, content: ContentItem) -> None:
    asset = db.scalar(select(MediaAsset).where(MediaAsset.content_id == content.id))
    if asset is None:
        asset = MediaAsset(content_id=content.id, download_status="pending")
        db.add(asset)
        db.flush()
    enqueue(db, "download", {"asset_id": asset.id, "url": content.url}, commit=False)


def _finish(
    db: Session, run: SyncRun, status: str, fetched: int = 0, added: int = 0, error: str = ""
) -> SyncRun:
    run.status = status
    run.fetched = fetched
    run.added = added
    run.error = error[:500]
    run.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run
