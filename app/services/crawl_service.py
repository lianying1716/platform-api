"""关键词采集服务：调 modules/crawler 搜索内容，去重入库 content_item，
存指标快照，按 to_candidate 决定是否给视频建下载素材。

抓取已全部抽离为纯 Python（bilibili/douyin/xhs/kuaishou/zhihu，见 modules/crawler/），
用账号中心的 Cookie 直接抓取；需要运行时签名的平台由受管浏览器完成。
没有可用账号或被风控时，crawl_run 记 error，不影响平台其他部分。
"""

import logging
import random
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import events
from app.core.crypto import decrypt_credential
from app.core.queue import enqueue
from app.models import (
    ContentItem,
    ContentMetricSnapshot,
    CrawlRun,
    CrawlTask,
    MediaAsset,
    PlatformAccount,
)
from app.modules import crawler
from app.services import foundation


def _pick_crawl_account(db: Session, platform: str, requested_id: int | None = None) -> PlatformAccount | None:
    """选本次采集账号：指定优先；否则最久未用优先、随机打散，避免固定用一个。"""
    if requested_id is not None:
        return db.scalar(
            select(PlatformAccount).where(
                PlatformAccount.id == requested_id,
                PlatformAccount.platform == platform,
                PlatformAccount.status == "active",
                PlatformAccount.is_system.is_(False),
            )
        )
    candidates = db.scalars(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.status == "active",
            PlatformAccount.is_system.is_(False),
        )
    ).all()
    if not candidates:
        return None
    random.shuffle(candidates)
    candidates.sort(key=lambda a: (a.last_crawl_used_at is not None, a.last_crawl_used_at or datetime.min))
    return candidates[0]


def _account_cookie(account: PlatformAccount | None) -> str:
    if account is None or account.credential is None:
        return ""
    cred = decrypt_credential(account.credential.ciphertext)
    # B站凭证是分字段的，拼成 cookie 串；其余平台本就存整段 cookie
    if "sessdata" in cred:
        parts = [f"SESSDATA={cred.get('sessdata','')}", f"bili_jct={cred.get('bili_jct','')}",
                 f"buvid3={cred.get('buvid3','')}", f"DedeUserID={cred.get('dedeuserid','')}"]
        return "; ".join(p for p in parts if not p.endswith("="))
    return cred.get("cookie", "")


async def run_crawl_task(db: Session, task_id: int) -> CrawlRun:
    task = db.get(CrawlTask, task_id)
    if task is None:
        raise ValueError("采集任务不存在")

    run = CrawlRun(task_id=task.id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    account = _pick_crawl_account(db, task.platform, task.account_id)
    run.account_id = account.id if account else None
    db.commit()

    if account is None:
        want = f"指定的账号 #{task.account_id}" if task.account_id else "该平台没有可用的 active 账号"
        return _finish(db, run, "error", error=f"没有可用于采集的账号：{want}")

    try:
        cookie = _account_cookie(account)
        items = await crawler.search(
            platform=task.platform,
            keyword=task.keyword,
            sort=task.sort,
            limit=task.limit_per_run,
            with_comments=task.with_comments,
            cookie=cookie,
        )
    except Exception as exc:  # noqa: BLE001 抓取异常不应让平台崩
        return _finish(db, run, "error", error=f"抓取失败: {exc}")

    added = 0
    touched: list[ContentItem] = []
    for item in items:
        if task.min_play and item.get("metrics", {}).get("play", 0) < task.min_play:
            continue
        content, is_new = _upsert(db, task, item, account.id if account else None)
        _persist_foundation(db, task.platform, "crawl", content, item)
        _record_metric(db, content, item)
        touched.append(content)
        if is_new:
            added += 1
            if task.to_candidate and content.content_type == "video":
                _ensure_download(db, content)
    task.last_run_at = datetime.utcnow()
    if account is not None:
        account.last_crawl_used_at = datetime.utcnow()
    db.commit()
    events.publish("crawl_done", {"task_id": task.id, "added": added})
    # 评论富集：动态门控后入队（不阻塞，worker 抓）。指标快照已在上方 commit，门控才读得到。
    from app.core.comment_settings import get_comment_settings
    from app.services.comment_gate import enqueue_comment_jobs

    try:
        enqueue_comment_jobs(db, touched, platform=task.platform, settings=get_comment_settings(db))
    except Exception:  # noqa: BLE001 评论富集是增强项，失败不得翻转本次采集的成功状态
        db.rollback()
        logging.getLogger("platform.crawl").exception("评论入队失败，已忽略（不影响本次采集）")
    # 动态分档加工：热度门控决定是否升到高级档后自动入队（设计 §6.1）。只对**已下载完成**的素材入队，
    # 新采内容的视频尚未下载，将在下载完成后由 handle_download 再走一次门控。
    from app.core.process_settings import get_process_settings
    from app.services.process_gate import enqueue_process_jobs

    try:
        enqueue_process_jobs(db, touched, settings=get_process_settings(db))
    except Exception:  # noqa: BLE001 分档加工是增强项，失败不得翻转本次采集的成功状态
        db.rollback()
        logging.getLogger("platform.crawl").exception("分档入队失败，已忽略（不影响本次采集）")
    return _finish(db, run, "success", fetched=len(items), added=added)


def _upsert(
    db: Session, task: CrawlTask, item: dict, account_id: int | None
) -> tuple[ContentItem, bool]:
    if account_id is None:
        raise RuntimeError("没有可用于采集的账号（跨账号独立素材要求内容必须有账号归属）")
    # 同 sync：长文案按列宽截断，避免超长写库报错。
    item = foundation.normalize_content_fields(item)
    existing = db.scalar(
        select(ContentItem).where(
            ContentItem.platform == task.platform,
            ContentItem.external_id == item["external_id"],
            ContentItem.account_id == account_id,
        )
    )
    if existing:
        return existing, False
    content = ContentItem(
        account_id=account_id,
        platform=task.platform,
        external_id=item["external_id"],
        content_type=item.get("content_type", "video"),
        title=item.get("title", ""),
        author_name=item.get("author_name", ""),
        author_id=item.get("author_id", ""),
        url=item.get("url", ""),
        cover_url=item.get("cover_url", ""),
        published_at=item.get("published_at"),
        source_kind="crawl",
        source_id=task.id,
        extra=item.get("extra", {}),
    )
    db.add(content)
    db.flush()
    return content, True


def _persist_foundation(
    db: Session, platform: str, source: str, content: ContentItem, item: dict
) -> None:
    """地基写入（creator_profile + raw_payload）。

    增强项：失败只记日志，**绝不翻转本次采集的成功状态**。隔离靠 foundation 内部
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
            raw=item.get("author_raw") or item.get("extra") or {},
        )
        foundation.record_raw(
            db, platform, source, item.get("external_id", ""), item, content_id=content.id
        )
    except Exception:  # noqa: BLE001 地基写入是增强项，失败不得翻转本次采集的成功状态
        foundation.rollback_if_broken(db)
        logging.getLogger("platform.crawl").exception("地基写入失败，已忽略（不影响本次采集）")


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
    db: Session, run: CrawlRun, status: str, fetched: int = 0, added: int = 0, error: str = ""
) -> CrawlRun:
    run.status = status
    run.fetched = fetched
    run.added = added
    run.error = error[:500]
    run.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run
