"""关键词采集服务：调 modules/crawler 搜索内容，去重入库 content_item，
存指标快照，按 to_candidate 决定是否给视频建下载素材。

抓取已抽离为纯 Python（bilibili/douyin/xhs，见 modules/crawler/），用账号中心的 Cookie
直接 HTTP 抓取，不依赖活浏览器。其余平台回退 MediaCrawler 子进程。
没有可用账号或被风控时，crawl_run 记 error，不影响平台其他部分。
"""

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


def _platform_cookie(db: Session, platform: str) -> str:
    """取该平台一个在线账号的 cookie 字符串（纯 Python 抓取需要）。"""
    account = db.scalar(
        select(PlatformAccount)
        .where(PlatformAccount.platform == platform, PlatformAccount.status == "active")
        .order_by(PlatformAccount.id)
    )
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

    try:
        cookie = _platform_cookie(db, task.platform)
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
    for item in items:
        if task.min_play and item.get("metrics", {}).get("play", 0) < task.min_play:
            continue
        content, is_new = _upsert(db, task, item)
        _record_metric(db, content, item)
        if is_new:
            added += 1
            if task.to_candidate and content.content_type == "video":
                _ensure_download(db, content)
    task.last_run_at = datetime.utcnow()
    db.commit()
    events.publish("crawl_done", {"task_id": task.id, "added": added})
    return _finish(db, run, "success", fetched=len(items), added=added)


def _upsert(db: Session, task: CrawlTask, item: dict) -> tuple[ContentItem, bool]:
    existing = db.scalar(
        select(ContentItem).where(
            ContentItem.platform == task.platform, ContentItem.external_id == item["external_id"]
        )
    )
    if existing:
        return existing, False
    content = ContentItem(
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
