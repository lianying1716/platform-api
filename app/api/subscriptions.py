"""订阅同步 API（P2）。

- 列某账号的远端源（收藏夹/关注/合集），需有效凭证。
- 订阅 CRUD：勾选要跟踪的源。
- 手动触发同步 / 查看同步历史。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.crypto import decrypt_credential
from app.db import get_db
from app.models import PlatformAccount, Subscription, SyncRun
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
    interval_minutes: int = 360
    subdir_template: str = "{platform}/{creator}/{date}"


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
    last_sync_at: str | None = None

    model_config = {"from_attributes": True}


@router.get("/accounts/{account_id}/remote-sources")
async def remote_sources(account_id: int, db: Session = Depends(get_db)):
    """拉取账号的收藏夹/关注/合集树。需要有效凭证。"""
    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.credential is None:
        raise HTTPException(status_code=400, detail="账号未录入凭证")
    adapter = get_adapter(account.platform)
    lister = getattr(adapter, "list_sources", None)
    if lister is None:
        raise HTTPException(status_code=400, detail=f"{adapter.label} 暂不支持列出订阅源")
    try:
        credential = decrypt_credential(account.credential.ciphertext)
        return await lister(credential)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"拉取远端源失败: {exc}")


@router.get("/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(db: Session = Depends(get_db)):
    return db.scalars(select(Subscription).order_by(Subscription.id)).all()


@router.post("/subscriptions", response_model=SubscriptionOut)
def create_subscription(body: SubscriptionIn, db: Session = Depends(get_db)):
    account = db.get(PlatformAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    sub = Subscription(platform=account.platform, **body.model_dump())
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.put("/subscriptions/{sub_id}", response_model=SubscriptionOut)
def update_subscription(sub_id: int, body: SubscriptionIn, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    for k, v in body.model_dump().items():
        setattr(sub, k, v)
    db.commit()
    db.refresh(sub)
    return sub


@router.delete("/subscriptions/{sub_id}")
def delete_subscription(sub_id: int, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    db.delete(sub)
    db.commit()
    return {"ok": True}


@router.post("/subscriptions/{sub_id}/sync")
async def trigger_sync(sub_id: int, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None:
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
