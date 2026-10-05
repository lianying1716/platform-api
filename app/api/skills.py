"""外部 Skill API（P4）：供 AI / HTTP 客户端用 API Key 调用的高层能力。

与前端共用底层逻辑，但走 API Key 鉴权 + scope 控权 + 调用日志（红线：API 即产品）。
能力对应 MCP 工具，约 12 个：账号状态、扫码登录、采集任务、订阅同步、素材查询、转录、看板、导出。

安全边界（沿用 final-review 禁令）：
- 不开放任意路径 / 任意 shell / 任意 headers 参数。
- AI 永远拿不到明文凭证：所有返回脱敏。
"""

import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.api_auth import require_scope
from app.db import get_db
from app.models import (
    ApiCallLog,
    ApiKey,
    ContentItem,
    CrawlTask,
    MediaAsset,
    PlatformAccount,
    Subscription,
    Transcript,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _log(db: Session, key_id: int | None, tool: str, params: str, status: str, ms: int) -> None:
    db.add(
        ApiCallLog(
            key_id=key_id, tool=tool, params_summary=params[:500], status=status, duration_ms=ms
        )
    )
    db.commit()


# ---------- 查询类（read scope） ----------
@router.get("/accounts")
def skill_list_accounts(
    request: Request, db: Session = Depends(get_db), key: ApiKey = Depends(require_scope("read", "operate", "admin"))
):
    t0 = time.time()
    accounts = db.scalars(select(PlatformAccount)).all()
    out = [
        {
            "id": a.id,
            "platform": a.platform,
            "display_name": a.display_name,
            "status": a.status,
            "status_message": a.status_message,
            "last_checked_at": a.last_checked_at.isoformat() if a.last_checked_at else None,
        }
        for a in accounts
    ]
    _log(db, key.id, "list_accounts", "", "ok", int((time.time() - t0) * 1000))
    return out


@router.get("/assets")
def skill_search_assets(
    request: Request,
    platform: str | None = None,
    keyword: str | None = None,
    transcribe_status: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("read", "operate", "admin")),
):
    t0 = time.time()
    stmt = select(MediaAsset, ContentItem).join(ContentItem, MediaAsset.content_id == ContentItem.id)
    if platform:
        stmt = stmt.where(ContentItem.platform == platform)
    if keyword:
        stmt = stmt.where(ContentItem.title.contains(keyword))
    if transcribe_status:
        stmt = stmt.where(MediaAsset.transcribe_status == transcribe_status)
    stmt = stmt.order_by(MediaAsset.id.desc()).limit(min(limit, 100))
    rows = db.execute(stmt).all()
    out = [
        {
            "asset_id": a.id,
            "title": c.title,
            "platform": c.platform,
            "author_name": c.author_name,
            "transcribe_status": a.transcribe_status,
            "analyze_status": a.analyze_status,
        }
        for a, c in rows
    ]
    _log(db, key.id, "search_assets", f"platform={platform} kw={keyword}", "ok", int((time.time() - t0) * 1000))
    return out


@router.get("/assets/{asset_id}/transcript")
def skill_get_transcript(
    asset_id: int,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("read", "operate", "admin")),
):
    t0 = time.time()
    transcript = db.scalar(select(Transcript).where(Transcript.asset_id == asset_id))
    if transcript is None:
        _log(db, key.id, "get_transcript", f"asset={asset_id}", "error", int((time.time() - t0) * 1000))
        raise HTTPException(status_code=404, detail="该素材尚无转录")
    _log(db, key.id, "get_transcript", f"asset={asset_id}", "ok", int((time.time() - t0) * 1000))
    return {"language": transcript.language, "full_text": transcript.full_text, "segments": transcript.segments}


# ---------- 操作类（operate scope） ----------
class CreateCrawlBody(BaseModel):
    platform: str
    keyword: str
    sort: str = "popular"
    limit_per_run: int = 50
    interval_minutes: int = 1440


@router.post("/crawl-tasks")
def skill_create_crawl_task(
    body: CreateCrawlBody,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("operate", "admin")),
):
    t0 = time.time()
    task = CrawlTask(
        platform=body.platform,
        keyword=body.keyword,
        sort=body.sort,
        limit_per_run=body.limit_per_run,
        interval_minutes=body.interval_minutes,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _log(db, key.id, "create_crawl_task", f"{body.platform}:{body.keyword}", "ok", int((time.time() - t0) * 1000))
    return {"task_id": task.id}


@router.post("/subscriptions/{subscription_id}/sync")
def skill_trigger_sync(
    subscription_id: int,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("operate", "admin")),
):
    t0 = time.time()
    from app.core.queue import enqueue

    sub = db.get(Subscription, subscription_id)
    if sub is None:
        _log(db, key.id, "trigger_sync", f"sub={subscription_id}", "error", int((time.time() - t0) * 1000))
        raise HTTPException(status_code=404, detail="订阅不存在")
    job = enqueue(db, "sync", {"subscription_id": subscription_id})
    _log(db, key.id, "trigger_sync", f"sub={subscription_id}", "ok", int((time.time() - t0) * 1000))
    return {"job_id": job.id}


@router.post("/accounts/qr-login")
async def skill_start_qr_login(
    platform: str,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("operate", "admin")),
):
    """发起扫码登录，返回 session_id + 二维码 base64（AI 直接展示给用户）。"""
    t0 = time.time()
    from app.config import settings
    from app.modules import get_adapter
    from app.modules.browser_qr import BrowserQrUnavailable
    from app.services import qr_sessions

    try:
        adapter = get_adapter(platform)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not adapter.supports_qr:
        raise HTTPException(status_code=400, detail=f"{adapter.label} 不支持扫码，请用 update_cookie")
    try:
        start = await adapter.qr_start()
    except BrowserQrUnavailable as exc:
        _log(db, key.id, "start_qr_login", platform, "error", int((time.time() - t0) * 1000))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _log(db, key.id, "start_qr_login", platform, "error", int((time.time() - t0) * 1000))
        raise HTTPException(status_code=502, detail=f"发起扫码失败: {exc}") from exc
    session_id = qr_sessions.create(platform, start.session_data)
    _log(db, key.id, "start_qr_login", platform, "ok", int((time.time() - t0) * 1000))
    return {
        "session_id": session_id,
        "platform": platform,
        "qr_image_base64": start.qr_image_b64,
        "expires_in": settings.qr_session_ttl,
    }


@router.post("/import-article")
async def skill_import_article(
    request: Request,
    body: dict,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("operate", "admin")),
):
    """导入单篇公众号文章（API Key 通道，与前端能力对等）。"""
    from app.modules.article_import import ArticleImportError
    from app.services.import_service import import_wechat_article

    t0 = time.time()
    url = (body or {}).get("url", "")
    try:
        asset, _created = await import_wechat_article(db, url)
        _log(db, key.id, "import_article", url[:200], "ok", int((time.time() - t0) * 1000))
        return {"asset_id": asset.id, "content_id": asset.content_id}
    except ArticleImportError as exc:
        _log(db, key.id, "import_article", url[:200], "error", int((time.time() - t0) * 1000))
        raise HTTPException(status_code=422, detail=str(exc))
