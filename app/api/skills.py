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
    accounts = db.scalars(
        select(PlatformAccount).where(PlatformAccount.is_system.is_(False))
    ).all()
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
    account_id: int | None = None,
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
    if account_id is not None:
        stmt = stmt.where(ContentItem.account_id == account_id)
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
            "account_id": c.account_id,
            "author_name": c.author_name,
            "transcribe_status": a.transcribe_status,
            "analyze_status": a.analyze_status,
        }
        for a, c in rows
    ]
    _log(db, key.id, "search_assets", f"platform={platform} kw={keyword}", "ok", int((time.time() - t0) * 1000))
    return out


@router.get("/crawl-tasks")
def skill_list_crawl_tasks(
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("read", "operate", "admin")),
):
    """列出关键词采集任务（与前端 /api/crawl-tasks 对齐，只读视图）。"""
    t0 = time.time()
    tasks = db.scalars(select(CrawlTask).order_by(CrawlTask.id.desc())).all()
    out = [
        {
            "id": t.id,
            "platform": t.platform,
            "keyword": t.keyword,
            "sort": t.sort,
            "limit_per_run": t.limit_per_run,
            "enabled": t.enabled,
            "interval_minutes": t.interval_minutes,
            "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
        }
        for t in tasks
    ]
    _log(db, key.id, "list_crawl_tasks", "", "ok", int((time.time() - t0) * 1000))
    return out


@router.get("/analytics")
def skill_get_analytics(
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("read", "operate", "admin")),
):
    """数据看板摘要（复用与前端 /api/dashboard/summary 相同的聚合逻辑）。"""
    from app.api.analytics import dashboard_summary

    t0 = time.time()
    data = dashboard_summary(db)
    _log(db, key.id, "get_analytics", "", "ok", int((time.time() - t0) * 1000))
    return data


@router.get("/followed-creators")
def skill_list_followed_creators(
    platform: str | None = None,
    account_id: int | None = None,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("read", "operate", "admin")),
):
    """列出关注博主及同步状态（供 AI 决策"该同步谁"）。

    复用前端 /api/followed-creators 的投影逻辑（creator 订阅 + 博主资料），
    保证 AI 看到的 id 与前端一致，可直接用于 trigger_sync / 博主同步。
    """
    from app.api.followed_creators import list_followed_creators as _list_fc

    t0 = time.time()
    rows = _list_fc(platform=platform, account_id=account_id, db=db)
    _log(db, key.id, "list_followed_creators", f"platform={platform}", "ok", int((time.time() - t0) * 1000))
    return rows


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
    account_id: int | None = None
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
    if body.account_id is not None:
        account = db.get(PlatformAccount, body.account_id)
        if (
            account is None
            or account.is_system
            or account.platform != body.platform
            or account.status != "active"
        ):
            _log(db, key.id, "create_crawl_task", "bad_account", "error", int((time.time() - t0) * 1000))
            raise HTTPException(status_code=422, detail="指定账号不存在、平台不匹配或不是可用账号")
    task = CrawlTask(
        platform=body.platform,
        keyword=body.keyword,
        account_id=body.account_id,
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
    if sub is None or sub.deleted_at is not None:
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


@router.get("/accounts/qr-login/{session_id}")
async def skill_poll_qr_login(
    session_id: str,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("operate", "admin")),
):
    """轮询扫码状态：pending/scanned/success/expired。

    与前端 /api/accounts/qr-login/{id} 完全等价（复用同一逻辑），
    成功后账号自动入库，返回 account_id。
    """
    from app.api.accounts import qr_login_poll

    t0 = time.time()
    try:
        result = await qr_login_poll(session_id, db)
    except HTTPException as exc:
        _log(db, key.id, "poll_qr_login", session_id, "error", int((time.time() - t0) * 1000))
        raise
    _log(db, key.id, "poll_qr_login", session_id, "ok", int((time.time() - t0) * 1000))
    return result.model_dump() if hasattr(result, "model_dump") else result


@router.post("/accounts/cookie")
def skill_update_cookie(
    body: dict,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("operate", "admin")),
):
    """手动用 Cookie 录入/更新账号（扫码不可用时的兜底，与前端录入等价）。

    body: { platform, cookie, display_name? }
    按 (platform) 找已有账号则更新凭证，否则新建。
    """
    from datetime import datetime

    from app.api.accounts import _save_credential
    from app.modules import get_adapter

    t0 = time.time()
    platform = (body or {}).get("platform", "")
    cookie = (body or {}).get("cookie", "")
    display_name = (body or {}).get("display_name", "")
    try:
        adapter = get_adapter(platform)
    except ValueError as exc:
        _log(db, key.id, "update_cookie", platform, "error", int((time.time() - t0) * 1000))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        plain = adapter.parse_manual_input({"cookie": cookie})
    except ValueError as exc:
        _log(db, key.id, "update_cookie", platform, "error", int((time.time() - t0) * 1000))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    account = db.scalar(
        select(PlatformAccount)
        .where(
            PlatformAccount.platform == platform,
            PlatformAccount.is_system.is_(False),
            PlatformAccount.status != "deleted",
        )
        .order_by(PlatformAccount.id)
    )
    if account is None:
        account = PlatformAccount(
            platform=platform, display_name=display_name, credential_kind=adapter.credential_kind
        )
        db.add(account)
        db.flush()
    elif display_name:
        account.display_name = display_name
    _save_credential(db, account, plain)
    account.status, account.status_message = "unchecked", ""
    db.commit()
    db.refresh(account)
    _log(db, key.id, "update_cookie", platform, "ok", int((time.time() - t0) * 1000))
    return {"account_id": account.id, "platform": account.platform, "status": account.status}


@router.post("/accounts/{account_id}/check")
async def skill_check_account(
    account_id: int,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("operate", "admin")),
):
    """立即对某账号做一次登录态体检。"""
    from app.api.accounts import _run_check

    t0 = time.time()
    account = db.get(PlatformAccount, account_id)
    if account is None or account.is_system or account.status == "deleted":
        _log(db, key.id, "check_account", f"id={account_id}", "error", int((time.time() - t0) * 1000))
        raise HTTPException(status_code=404, detail="账号不存在")
    result = await _run_check(db, account, actor=f"apikey:{key.id}")
    _log(db, key.id, "check_account", f"id={account_id}", "ok", int((time.time() - t0) * 1000))
    return result.model_dump() if hasattr(result, "model_dump") else result


class ExportBody(BaseModel):
    asset_ids: list[int]
    format: str = "md"


@router.post("/assets/export")
def skill_export_assets(
    body: ExportBody,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("read", "operate", "admin")),
):
    """导出素材的转录+分析（md/json），与前端 /api/assets/export 等价。"""
    from app.api.assets import ExportRequest, export_assets

    t0 = time.time()
    result = export_assets(ExportRequest(asset_ids=body.asset_ids, format=body.format), db)
    _log(db, key.id, "export_assets", f"n={len(body.asset_ids)} fmt={body.format}", "ok", int((time.time() - t0) * 1000))
    return result


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


@router.post("/assets/{asset_id}/delete")
def skill_delete_asset(
    asset_id: int,
    body: dict,
    db: Session = Depends(get_db),
    key: ApiKey = Depends(require_scope("operate", "admin")),
):
    """外部 Agent 删除素材：必须 confirm=true，先写删除审计再物理删除。"""
    from app.models import ContentItem, DeletionAudit, MediaAsset

    t0 = time.time()
    if (body or {}).get("confirm") is not True:
        raise HTTPException(status_code=422, detail="删除素材必须显式确认：confirm=true")
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    content = db.get(ContentItem, asset.content_id)
    db.add(
        DeletionAudit(
            asset_id=asset.id,
            content_id=asset.content_id,
            account_id=content.account_id if content else None,
            actor=f"apikey:{key.id}",
            reason=str((body or {}).get("reason") or "")[:500],
        )
    )
    db.delete(asset)
    db.commit()
    _log(db, key.id, "delete_asset", f"asset_id={asset_id}", "ok", int((time.time() - t0) * 1000))
    return {"ok": True, "asset_id": asset_id, "confirmed": True}
