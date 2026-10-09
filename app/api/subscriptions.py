"""订阅同步 API（P2）。

- 列某账号的远端源（收藏夹/关注/合集），需有效凭证。
- 订阅 CRUD：勾选要跟踪的源。
- 手动触发同步 / 查看同步历史。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.crypto import decrypt_credential
from app.db import get_db
from app.models import ContentSource, CreatorProfile, PlatformAccount, Subscription, SyncRun
from app.modules import get_adapter
from app.services.sync_service import run_subscription_sync

router = APIRouter(prefix="/api", tags=["subscriptions"], dependencies=[Depends(security.require_admin)])


class SubscriptionIn(BaseModel):
    account_id: int
    source_type: str
    remote_id: str
    title: str = ""
    enabled: bool = True
    auto_download: bool = False
    interval_minutes: int = Field(360, ge=10)
    subdir_template: str = "{platform}/{creator}/{date}"
    # 同步设置（A1/A4）：范围校验在 create/update 两处同时生效（同一个模型）。
    min_play: int = Field(0, ge=0)
    publish_after_days: int = Field(0, ge=0)
    max_items: int = Field(50, ge=1, le=500)
    only_new: bool = True
    download_quality: str = "best"
    with_comments: bool = False

    @field_validator("download_quality")
    @classmethod
    def _check_download_quality(cls, value: str) -> str:
        allowed = {"best", "1080", "720", "audio"}
        if value not in allowed:
            raise ValueError(f"download_quality 必须是 {sorted(allowed)} 之一")
        return value


class SubscriptionOut(BaseModel):
    id: int
    account_id: int
    platform: str
    source_type: str
    remote_id: str
    title: str
    enabled: bool
    auto_download: bool
    interval_minutes: int
    subdir_template: str = "{platform}/{creator}/{date}"
    min_play: int = 0
    publish_after_days: int = 0
    max_items: int = 50
    only_new: bool = True
    download_quality: str = "best"
    with_comments: bool = False
    last_sync_at: datetime | None = None
    deleted_at: datetime | None = None
    # creator 订阅即"关注的博主"；其头像供列表直接显示
    creator_avatar: str = ""

    model_config = {"from_attributes": True}


@router.get("/accounts/{account_id}/remote-sources")
async def remote_sources(account_id: int, source_type: str = "favorite", db: Session = Depends(get_db)):
    """拉取账号的收藏夹/合集/关注列表（source_type: favorite/collection/creator）。需要有效凭证。"""
    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.is_system or account.status == "deleted":
        raise HTTPException(status_code=422, detail="账号已停用、系统账号或不可用")
    if account.credential is None:
        raise HTTPException(status_code=400, detail="账号未录入凭证")
    adapter = get_adapter(account.platform)
    lister = getattr(adapter, "list_sources", None)
    if lister is None:
        raise HTTPException(status_code=400, detail=f"{adapter.label} 暂不支持列出订阅源")
    try:
        credential = decrypt_credential(account.credential.ciphertext)
        return await lister(credential, source_type)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"拉取远端源失败: {exc}")


@router.get("/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(db: Session = Depends(get_db)):
    subs = db.scalars(
        select(Subscription).where(Subscription.deleted_at.is_(None)).order_by(Subscription.id)
    ).all()
    # creator 订阅补博主头像（资料在 creator_profile；关系状态就在 subscription 本身）
    creator_subs = [s for s in subs if s.source_type == "creator"]
    if creator_subs:
        profiles = db.scalars(
            select(CreatorProfile).where(
                CreatorProfile.author_id.in_([s.remote_id for s in creator_subs])
            )
        ).all()
        pmap = {(p.platform, p.author_id): p for p in profiles}
        for sub in creator_subs:
            p = pmap.get((sub.platform, sub.remote_id))
            if p is not None:
                sub.creator_avatar = p.avatar
    return subs


@router.post("/subscriptions", response_model=SubscriptionOut)
def create_subscription(body: SubscriptionIn, db: Session = Depends(get_db)):
    account = db.get(PlatformAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.is_system or account.status != "active":
        raise HTTPException(status_code=422, detail="账号已停用、系统账号或不可用，无法创建订阅")
    sub = Subscription(platform=account.platform, **body.model_dump())
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.put("/subscriptions/{sub_id}", response_model=SubscriptionOut)
def update_subscription(sub_id: int, body: SubscriptionIn, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None or sub.deleted_at is not None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    account = db.get(PlatformAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.is_system or account.status != "active":
        raise HTTPException(status_code=422, detail="账号已停用、系统账号或不可用，无法绑定订阅")
    for k, v in body.model_dump().items():
        setattr(sub, k, v)
    db.commit()
    db.refresh(sub)
    return sub


@router.delete("/subscriptions/{sub_id}")
def delete_subscription(sub_id: int, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None or sub.deleted_at is not None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    # 保留素材：只停用该订阅产生的来源关系，订阅行保留为历史。
    sources = db.scalars(select(ContentSource).where(ContentSource.subscription_id == sub.id)).all()
    for source in sources:
        source.active = False
    sub.enabled = False
    sub.deleted_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "deactivated_sources": len(sources), "kept_contents": len(sources)}


@router.post("/subscriptions/{sub_id}/sync")
async def trigger_sync(sub_id: int, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None or sub.deleted_at is not None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    run = await run_subscription_sync(db, sub)
    return {"run_id": run.id, "status": run.status, "fetched": run.fetched, "added": run.added, "error": run.error}


@router.get("/subscriptions/{sub_id}/runs")
def sync_runs(sub_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(SyncRun).where(SyncRun.subscription_id == sub_id).order_by(SyncRun.id.desc()).limit(50)
    ).all()
    return [
        {
            "id": r.id,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status,
            "fetched": r.fetched,
            "added": r.added,
            "error": r.error,
        }
        for r in rows
    ]
