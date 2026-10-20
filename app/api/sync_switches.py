"""账号级同步开关（收藏 / 稍后再看 / 关注列表）——把「要人工打开的同步」摆在最显眼处。

背景（对齐抖小云的独立 job 开关）：收藏、稍后再看、关注列表各自独立，
用户必须先手动打开某一类，系统才去同步它。本模块提供账号维度的一览与开关读写：

- ``GET  /api/accounts/{id}/sync-switches``            列出该账号的三类开关及状态
- ``PUT  /api/accounts/{id}/sync-switches/{key}``      打开/关闭某一类（收藏/稍后再看/关注列表）

约定：
- 收藏 / 稍后再看：默认**关闭**，必须手动打开；打开即建订阅（不存在时按平台的单例源创建）。
- 关注列表：默认**开启**（导入关注即跟踪关系），但「同步博主名下视频」是更高一级的
  手动开关，落在 ``subscription.sync_videos``，不在本模块的账号级开关里。
- 本模块只负责"开关状态"，不在这里发起拉取；"是否真的同步了"看 sync_run（含 updated）。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.crypto import decrypt_credential
from app.db import get_db
from app.models import PlatformAccount, Subscription
from app.modules import get_adapter

router = APIRouter(prefix="/api", tags=["sync-switches"], dependencies=[Depends(security.require_admin)])


class SwitchIn(BaseModel):
    enabled: bool
    only_new: bool | None = None


class SwitchListOut(BaseModel):
    account_id: int
    platform: str
    switches: list[dict]


class SwitchUpdateOut(BaseModel):
    key: str
    enabled: bool
    subscription_id: int | None = None
    only_new: bool | None = None
    affected: int | None = None


def _platform_switch_keys(platform: str) -> list[str]:
    supported = getattr(get_adapter(platform), "singleton_sync_sources", ())
    return [key for key in ("favorite", "watch_later") if key in supported] + ["creator"]


@router.get("/sync-switches/capabilities")
def switch_capabilities():
    """Return the account-level switch keys that exist for each platform."""
    from app.modules import REGISTRY

    return {platform: _platform_switch_keys(platform) for platform in REGISTRY}

# 账号级开关的稳定 key（前端按 key 渲染，不靠顺序）
_KIND_LABELS = {
    "favorite": ("收藏同步", "把「我的收藏」里的作品同步入库"),
    "watch_later": ("稍后再看", "把 B站「稍后再看」列表同步入库"),
}
_CREATOR_LABEL = ("关注列表", "跟踪我关注的博主；博主名下视频需要在卡片上单独开启")


def _active_creator_subs(db: Session, account_id: int) -> list[Subscription]:
    return list(
        db.scalars(
            select(Subscription).where(
                Subscription.account_id == account_id,
                Subscription.source_type == "creator",
                Subscription.deleted_at.is_(None),
            )
        ).all()
    )


def _singleton_sub(db: Session, account_id: int, source_type: str) -> Subscription | None:
    """取该账号下某个「单例源」订阅（收藏/稍后再看各最多一条），软删行也算在内以便复活。"""
    return db.scalars(
        select(Subscription).where(
            Subscription.account_id == account_id,
            Subscription.source_type == source_type,
        )
    ).first()


def _switch_view(db: Session, account: PlatformAccount, source_type: str) -> dict | None:
    adapter = get_adapter(account.platform)
    supported = getattr(adapter, "singleton_sync_sources", None)
    if supported is not None and source_type not in supported:
        return None
    if source_type in _KIND_LABELS:
        label, desc = _KIND_LABELS[source_type]
        sub = _singleton_sub(db, account.id, source_type)
        return {
            "key": source_type,
            "source_type": source_type,
            "label": label,
            "description": desc,
            "enabled": bool(sub.enabled) if sub else False,
            "exists": sub is not None and sub.deleted_at is None,
            "subscription_id": sub.id if sub and sub.deleted_at is None else None,
            "only_new": sub.only_new if sub else True,
            "last_sync_at": sub.last_sync_at.isoformat() if sub and sub.last_sync_at else None,
        }
    return None


@router.get("/accounts/{account_id}/sync-switches", response_model=SwitchListOut)
def list_switches(account_id: int, db: Session = Depends(get_db)):
    """列出账号的同步开关。收藏/稍后再看默认关闭，关注列表默认开启。"""
    account = db.get(PlatformAccount, account_id)
    if account is None or account.status == "deleted":
        raise HTTPException(status_code=404, detail="账号不存在或已停用")

    switches: list[dict] = []
    for source_type in ("favorite", "watch_later"):
        view = _switch_view(db, account, source_type)
        if view is not None:
            switches.append(view)

    creators = _active_creator_subs(db, account_id)
    switches.append(
        {
            "key": "creator",
            "source_type": "creator",
            "label": _CREATOR_LABEL[0],
            "description": _CREATOR_LABEL[1],
            "enabled": any(c.enabled for c in creators),
            "exists": bool(creators),
            "followed_count": len(creators),
            "video_sync_count": sum(1 for c in creators if c.sync_videos),
            "last_sync_at": max(
                (c.last_sync_at for c in creators if c.last_sync_at), default=None
            ).isoformat()
            if any(c.last_sync_at for c in creators)
            else None,
        }
    )
    return {"account_id": account.id, "platform": account.platform, "switches": switches}


@router.put("/accounts/{account_id}/sync-switches/{key}", response_model=SwitchUpdateOut)
async def update_switch(account_id: int, key: str, body: SwitchIn, db: Session = Depends(get_db)):
    """打开/关闭一类同步开关。打开收藏/稍后再看时按需创建订阅（幂等）。"""
    account = db.get(PlatformAccount, account_id)
    if account is None or account.status == "deleted":
        raise HTTPException(status_code=404, detail="账号不存在或已停用")

    if key == "creator":
        creators = _active_creator_subs(db, account_id)
        if not creators:
            raise HTTPException(status_code=400, detail="还没有关注列表，请先点「导入我的关注」")
        for sub in creators:
            sub.enabled = body.enabled
        db.commit()
        return {"key": "creator", "enabled": body.enabled, "affected": len(creators)}

    if key not in _KIND_LABELS:
        raise HTTPException(status_code=404, detail=f"未知的同步开关: {key}")

    view = _switch_view(db, account, key)
    if view is None:
        raise HTTPException(
            status_code=400,
            detail=f"{get_adapter(account.platform).label} 的「{_KIND_LABELS[key][0]}」不是账号级单例源",
        )

    sub = _singleton_sub(db, account.id, key)
    if sub is None:
        # 收藏是"整个账号"级别的单例源，remote_id 由平台决定（抖音为 sec_user_id）。
        if account.credential is None:
            raise HTTPException(status_code=400, detail="账号未录入凭证，请先在账号中心登录")
        adapter = get_adapter(account.platform)
        credential = decrypt_credential(account.credential.ciphertext)
        sources = await adapter.list_sources(credential, key)
        if not sources:
            raise HTTPException(status_code=400, detail=f"未取到「{_KIND_LABELS[key][0]}」的来源标识，请稍后重试")
        remote_id = str(sources[0].get("remote_id") or "").strip()
        if not remote_id:
            raise HTTPException(status_code=400, detail=f"未取到「{_KIND_LABELS[key][0]}」的来源标识，请稍后重试")
        sub = Subscription(
            account_id=account.id,
            platform=account.platform,
            source_type=key,
            remote_id=remote_id,
            title=str(sources[0].get("title") or _KIND_LABELS[key][0]),
            # Account-level sources must remain independently switchable.  If a
            # user just turned one off and the row is later recreated, opening it
            # must mean enabled; otherwise the UI reports success while the next
            # scheduler scan skips it.
            enabled=body.enabled,
        )
        db.add(sub)
    else:
        sub.enabled = body.enabled
        # 复活被移除过的来源：唯一约束 (account_id, source_type, remote_id) 不看 deleted_at
        if sub.deleted_at is not None:
            sub.deleted_at = None
    if body.only_new is not None:
        sub.only_new = body.only_new
    db.commit()
    db.refresh(sub)
    return {
        "key": key,
        "enabled": sub.enabled,
        "subscription_id": sub.id,
        "only_new": sub.only_new,
    }
