"""关注博主 API：关注列表（卡片数据源）、刷新入库、同步开关、立即同步。

- GET  /api/followed-creators?platform=&account_id=   读已入库的关注博主
- POST /api/followed-creators/refresh {account_id}     从平台拉取并 upsert
- PUT  /api/followed-creators/{id} {sync_enabled,full_sync}
- POST /api/followed-creators/{id}/sync                立即同步该博主

同步语义：sync_enabled=True 时维护一条 Subscription(source_type="creator")；
only_new = not full_sync（全量＝覆盖刷新，非全量＝只入库新发布）。不新增同步引擎，复用 sync_service。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import fetch_cache, security
from app.core.crypto import decrypt_credential
from app.db import get_db
from app.models import FollowedCreator, PlatformAccount, Subscription
from app.modules import get_adapter
from app.services.sync_service import run_subscription_sync

router = APIRouter(
    prefix="/api/followed-creators", tags=["followed-creators"],
    dependencies=[Depends(security.require_admin)],
)

# 支持「关注博主」的平台（其余平台关注列表暂未抽离）
_PLATFORMS = {"bilibili", "douyin"}

# 逐条补粉丝/获赞的上限（避免关注几百人时刷新过慢；超出留 0，UI 显示 —）
_STATS_ENRICH_CAP = 60


def _out(fc: FollowedCreator) -> dict:
    return {
        "id": fc.id,
        "account_id": fc.account_id,
        "platform": fc.platform,
        "author_id": fc.author_id,
        "author_name": fc.author_name,
        "avatar": fc.avatar,
        "home_url": fc.home_url,
        "signature": fc.signature,
        "verified": fc.verified,
        "follower_count": fc.follower_count,
        "like_count": fc.like_count,
        "sync_enabled": fc.sync_enabled,
        "full_sync": fc.full_sync,
        "last_synced_at": fc.last_synced_at.isoformat() if fc.last_synced_at else None,
    }


@router.get("")
def list_followed_creators(
    platform: str | None = None, account_id: int | None = None, db: Session = Depends(get_db)
):
    stmt = select(FollowedCreator).order_by(FollowedCreator.avatar == "", FollowedCreator.id)
    if platform:
        stmt = stmt.where(FollowedCreator.platform == platform)
    if account_id:
        stmt = stmt.where(FollowedCreator.account_id == account_id)
    return [_out(fc) for fc in db.scalars(stmt).all()]


class RefreshIn(BaseModel):
    account_id: int
    force: bool = False  # 跳过缓存强制拉取（仍受风控冷却限制）


def _followings_key(account) -> str:
    return f"followings:{account.platform}:{account.id}"


def _cooldown_key(account, op: str) -> str:
    return f"cooldown:{account.platform}:{account.id}:{op}"


async def _enrich_stats(db: Session, adapter, account, credential, items: list[dict]) -> None:
    """按 24h 缓存补全粉丝/获赞（仅对有该能力的平台）。失败静默留 0。"""
    fetcher = getattr(adapter, "fetch_follower_count", None)
    if fetcher is None:
        return
    for item in items[:_STATS_ENRICH_CAP]:
        author_id = str(item.get("author_id") or "").strip()
        if not author_id:
            continue
        key = f"stats:{account.platform}:{author_id}"
        cached = fetch_cache.get_cache(db, key)
        if cached is not None:
            item["follower_count"] = int(cached.get("follower_count") or item.get("follower_count") or 0)
            continue
        try:
            count = await fetcher(credential, author_id)
        except Exception:  # noqa: BLE001 统计是增强项，失败不影响关注列表
            count = 0
        item["follower_count"] = count
        fetch_cache.set_cache(db, key, {"follower_count": count}, ttl_seconds=fetch_cache.TTL_STATS)
    db.commit()


@router.post("/refresh")
async def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    """刷新关注列表：默认走 6h 缓存（不频繁拉取）；风控冷却期内只读缓存。

    返回 ``cached`` 表示是否来自缓存、``cooling`` 表示是否处于风控冷却。
    """
    account = db.get(PlatformAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.platform not in _PLATFORMS:
        raise HTTPException(status_code=400, detail=f"暂不支持拉取 {account.platform} 的关注列表")
    if account.credential is None:
        raise HTTPException(status_code=400, detail="账号未录入凭证，请先在账号中心登录")

    adapter = get_adapter(account.platform)
    lister = getattr(adapter, "list_followings", None)
    if lister is None:
        raise HTTPException(status_code=400, detail=f"{adapter.label} 暂不支持拉取关注列表")

    cache_key = _followings_key(account)
    cooldown_key = _cooldown_key(account, "followings")
    cached = fetch_cache.get_cache(db, cache_key)
    cooling = fetch_cache.is_cooling(db, cooldown_key)

    from_cache = False
    if cached is not None and (not body.force or cooling):
        items = cached.get("items") or []
        from_cache = True
    elif cooling:
        remain = fetch_cache.cooldown_remaining(db, cooldown_key)
        raise HTTPException(
            status_code=429,
            detail=f"平台风控冷却中，约 {max(1, remain // 60)} 分钟后可再拉取（当前无缓存可展示）",
        )
    else:
        credential = decrypt_credential(account.credential.ciphertext)

        async def _fetch():
            return await lister(credential)

        try:
            items = await fetch_cache.single_flight(cache_key, _fetch)
        except Exception as exc:  # noqa: BLE001
            if fetch_cache.looks_like_risk(str(exc)):
                fetch_cache.set_cooldown(db, cooldown_key)
                db.commit()
                remain = fetch_cache.cooldown_remaining(db, cooldown_key)
                raise HTTPException(
                    status_code=502,
                    detail=f"平台风控：{exc}（已冷却约 {max(1, remain // 60)} 分钟）",
                )
            raise HTTPException(status_code=502, detail=f"拉取关注列表失败: {exc}")
        # 富化统计（24h 缓存；失败静默），再整体写 6h 缓存
        try:
            await _enrich_stats(db, adapter, account, credential, items)
        except Exception:  # noqa: BLE001 统计是增强项
            db.rollback()
        fetch_cache.set_cache(db, cache_key, {"items": items}, ttl_seconds=fetch_cache.TTL_FOLLOWINGS)
        db.commit()

    count = 0
    for item in items:
        author_id = str(item.get("author_id") or "").strip()
        if not author_id:
            continue
        fc = db.scalar(
            select(FollowedCreator).where(
                FollowedCreator.account_id == account.id, FollowedCreator.author_id == author_id
            )
        )
        if fc is None:
            fc = FollowedCreator(
                account_id=account.id, platform=account.platform, author_id=author_id
            )
            db.add(fc)
        # 刷新可变字段（空值不覆盖，避免接口缺字段时清空真实头像/昵称）
        for field in ("author_name", "avatar", "home_url", "signature"):
            value = item.get(field)
            if value:
                setattr(fc, field, value)
        fc.verified = bool(item.get("verified", fc.verified))
        if item.get("follower_count"):
            fc.follower_count = int(item["follower_count"])
        if item.get("like_count"):
            fc.like_count = int(item["like_count"])
        fc.raw = item.get("raw") or {}
        count += 1
    db.commit()
    return {"count": count, "cached": from_cache, "cooling": cooling}


class ToggleIn(BaseModel):
    sync_enabled: bool | None = None
    full_sync: bool | None = None


def _sync_subscription(db: Session, fc: FollowedCreator) -> Subscription | None:
    """按同步开关维护该博主的订阅（开启则建/启用，关闭则禁用）。"""
    sub = db.scalar(
        select(Subscription).where(
            Subscription.account_id == fc.account_id,
            Subscription.source_type == "creator",
            Subscription.remote_id == fc.author_id,
        )
    )
    if not fc.sync_enabled:
        if sub is not None:
            sub.enabled = False
        return sub
    if sub is None:
        sub = Subscription(
            account_id=fc.account_id,
            platform=fc.platform,
            source_type="creator",
            remote_id=fc.author_id,
            title=fc.author_name or fc.author_id,
        )
        db.add(sub)
    sub.enabled = True
    sub.title = fc.author_name or sub.title or fc.author_id
    sub.only_new = not fc.full_sync  # 全量＝覆盖刷新；否则只入库新发布
    return sub


@router.put("/{fc_id}")
def update_followed_creator(fc_id: int, body: ToggleIn, db: Session = Depends(get_db)):
    fc = db.get(FollowedCreator, fc_id)
    if fc is None:
        raise HTTPException(status_code=404, detail="关注博主不存在")
    if body.sync_enabled is not None:
        fc.sync_enabled = body.sync_enabled
    if body.full_sync is not None:
        fc.full_sync = body.full_sync
    _sync_subscription(db, fc)
    db.commit()
    db.refresh(fc)
    return _out(fc)


@router.post("/{fc_id}/sync")
async def sync_now(fc_id: int, db: Session = Depends(get_db)):
    """立即同步该博主：确保订阅存在并启用，然后执行一次同步。"""
    fc = db.get(FollowedCreator, fc_id)
    if fc is None:
        raise HTTPException(status_code=404, detail="关注博主不存在")
    fc.sync_enabled = True
    sub = _sync_subscription(db, fc)
    db.commit()
    db.refresh(sub)
    run = await run_subscription_sync(db, sub)
    fc.last_synced_at = datetime.utcnow()
    db.commit()
    return {
        "run_id": run.id,
        "status": run.status,
        "fetched": run.fetched,
        "added": run.added,
        "error": run.error,
    }
