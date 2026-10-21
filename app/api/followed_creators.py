"""关注列表 API：把「我跟踪的源」统一到 subscription 一张表上。

设计要点（消除与订阅源的重复）：
- **不再有独立的"关注博主"表**。一条 ``source_type='creator'`` 的 ``Subscription``
  就是"我关注了谁"；``enabled`` 即"是否跟踪这段关注关系"（导入即开启），
  ``sync_videos`` 即"是否同步该博主名下的视频"（默认关闭，必须手动打开），
  ``only_new`` 即"只同步新发布"。
- 博主资料（头像/昵称/简介/粉丝）按 ``(platform, author_id)`` 存 ``creator_profile``，
  与「同步开关」这类关系状态分表，各存一份，因此不可能出现两处开关互相漂移。
- 刷新关注列表 = 把平台上"我关注的人" upsert 成 creator 订阅（默认**开启关系跟踪**，
  与抖小云一致）；但"同步该博主名下视频"是更高一级的手动开关，默认关闭。
  刷新不会改动已有记录的开关。

接口：
- GET    /api/followed-creators            列出我关注的博主（按账号/平台过滤）
- POST   /api/followed-creators/refresh    从平台拉关注列表并入库（带缓存 + 风控冷却）
- PUT    /api/followed-creators/{id}       {sync_enabled, sync_videos, full_sync} → 写订阅
- POST   /api/followed-creators/{id}/sync  立即同步
- DELETE /api/followed-creators/{id}       取消关注
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.core import fetch_cache, security
from app.core.crypto import decrypt_credential
from app.db import get_db
from app.models import CreatorProfile, PlatformAccount, Subscription
from app.modules import get_adapter
from app.services.foundation import follower_count_of, upsert_creator
from app.application.commands.sync import SyncCommandError, enqueue_sync

router = APIRouter(
    prefix="/api/followed-creators", tags=["followed-creators"],
    dependencies=[Depends(security.require_admin)],
)

# 支持「关注列表」的平台（其余平台关注列表暂未抽离）
_PLATFORMS = {"bilibili", "douyin"}

# 逐条补粉丝/获赞的上限（避免关注几百人时刷新过慢；超出留 0，UI 显示 —）
_STATS_ENRICH_CAP = 60


def _profile_map(db: Session, rows: list[Subscription]) -> dict[tuple[str, str], CreatorProfile]:
    """按 (platform, remote_id) 取博主资料，一次性查完避免 N+1。"""
    keys = {(r.platform, r.remote_id) for r in rows if r.source_type == "creator"}
    if not keys:
        return {}
    profiles = db.scalars(
        select(CreatorProfile).where(CreatorProfile.author_id.in_([a for _p, a in keys]))
    ).all()
    return {(p.platform, p.author_id): p for p in profiles}


def _out(sub: Subscription, profile: CreatorProfile | None) -> dict:
    """把 creator 订阅 + 博主资料合成前端要的"关注博主"结构。

    这里的 ``sync_enabled`` / ``full_sync`` 都是订阅字段的**投影**，
    不再是另一份可独立写入的状态。
    """
    return {
        "id": sub.id,
        "account_id": sub.account_id,
        "platform": sub.platform,
        "author_id": sub.remote_id,
        "author_name": (profile.author_name if profile else "") or sub.title,
        # Keep the historical remote field for API clients; browsers use the
        # same-origin proxy field so they never issue cross-origin image calls.
        "avatar": profile.avatar if profile else "",
        "avatar_url": f"/api/media/avatars/{profile.id}" if profile and profile.avatar else "",
        "home_url": profile.homepage_url if profile else "",
        "signature": profile.bio if profile else "",
        "verified": bool(profile.verified) if profile else False,
        # 列值为 0 时回退原始快照：历史行不必等下次刷新就能显示真实粉丝数。
        "follower_count": follower_count_of(profile),
        "like_count": 0,
        "profile_id": profile.id if profile else None,
        # 关系状态：全部来自 subscription（唯一真相）
        "sync_enabled": sub.enabled,
        "sync_videos": sub.sync_videos,
        "full_sync": not sub.only_new,
        "last_synced_at": sub.last_sync_at.isoformat() if sub.last_sync_at else None,
    }


def _creator_subs(db: Session) -> list[Subscription]:
    return list(
        db.scalars(
            select(Subscription)
            .where(Subscription.source_type == "creator", Subscription.deleted_at.is_(None))
            .order_by(Subscription.id)
        ).all()
    )


@router.get("", response_model=list[schemas.FollowedCreatorOut])
def list_followed_creators(
    platform: str | None = None, account_id: int | None = None, db: Session = Depends(get_db)
):
    rows = _creator_subs(db)
    if platform:
        rows = [r for r in rows if r.platform == platform]
    if account_id:
        rows = [r for r in rows if r.account_id == account_id]
    profiles = _profile_map(db, rows)
    # 有头像的排前面（资料未补全的靠后）
    rows.sort(key=lambda r: (not bool((profiles.get((r.platform, r.remote_id)) or CreatorProfile()).avatar), r.id))
    return [_out(r, profiles.get((r.platform, r.remote_id))) for r in rows]


class RefreshIn(BaseModel):
    account_id: int
    force: bool = False  # 跳过缓存强制拉取（仍受风控冷却限制）


def _followings_key(account) -> str:
    return f"followings:{account.platform}:{account.id}"


def _cooldown_key(account, op: str) -> str:
    return f"cooldown:{account.platform}:{account.id}:{op}"


async def _enrich_stats(db: Session, adapter, account, credential, items: list[dict]) -> None:
    """按 24h 缓存补全粉丝数（仅对有该能力的平台）。

    这是**增强**而非覆盖：平台自己在关注列表响应里带回的 follower_count 是权威值，
    只有补到了更大的数字才覆盖。取不到就保留原值，
    绝不能因为一次补全失败就把真实粉丝数写成 0。
    """
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
            # 命中缓存：同样只在缓存里有有效数字时才覆盖
            cached_count = int(cached.get("follower_count") or 0)
            if cached_count:
                item["follower_count"] = cached_count
            continue
        try:
            count = int(await fetcher(credential, author_id))
        except Exception:  # noqa: BLE001 统计是增强项，失败不影响关注列表
            continue                      # 保留关注列表自带的粉丝数，不写 0
        if count:
            item["follower_count"] = count
            fetch_cache.set_cache(db, key, {"follower_count": count}, ttl_seconds=fetch_cache.TTL_STATS)
    db.commit()


@router.post("/refresh", response_model=schemas.FollowedRefreshOut)
async def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    """刷新关注列表：默认走 6h 缓存（不频繁拉取）；风控冷却期内只读缓存。

    只做「新增没见过的关注」+「更新已有关注的资料」，**不碰用户已设的开关**：
    新导入的关注默认开启关系跟踪（关注列表默认同步），但绝不改动已有记录的开关。
    平台列表可能因分页/风控只返回一部分，据此判取关会误关用户开着的同步。
    返回 ``cached``/``cooling`` 供 UI 提示。
    """
    account = db.get(PlatformAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.platform not in _PLATFORMS:
        raise HTTPException(status_code=400, detail=f"暂不支持拉取 {account.platform} 的关注列表")
    if account.is_system or account.status == "deleted":
        raise HTTPException(status_code=422, detail="账号已停用、系统账号或不可用")
    if not bool(account.sync_enabled):
        from app.services.account_state import SILENT_MESSAGE

        raise HTTPException(status_code=409, detail=SILENT_MESSAGE)
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
        try:
            await _enrich_stats(db, adapter, account, credential, items)
        except Exception:  # noqa: BLE001 统计是增强项
            db.rollback()
        fetch_cache.set_cache(db, cache_key, {"items": items}, ttl_seconds=fetch_cache.TTL_FOLLOWINGS)
        db.commit()

    # 已入库的 creator 订阅：按 remote_id 索引，用于判断"新增还是更新"。
    # 必须包含**软删**的行：唯一约束 (account_id, source_type, remote_id) 不看 deleted_at，
    # 若把它排除在外，重复关注同一个博主时既建不了新行、又不会复活旧行，会永久消失。
    existing = {
        s.remote_id: s
        for s in db.scalars(
            select(Subscription).where(
                Subscription.account_id == account.id, Subscription.source_type == "creator"
            )
        ).all()
    }

    count = 0
    for item in items:
        author_id = str(item.get("author_id") or "").strip()
        if not author_id:
            continue
        count += 1
        sub = existing.get(author_id)
        if sub is None:
            # 新关注：默认**开启关注关系跟踪**（对齐抖小云：关注列表默认同步），
            # 但「同步该博主名下视频」（sync_videos）默认关闭，需要用户手动打开。
            sub = Subscription(
                account_id=account.id,
                platform=account.platform,
                source_type="creator",
                remote_id=author_id,
                title=str(item.get("author_name") or author_id),
                enabled=True,
            )
            db.add(sub)
        else:
            # 之前取消关注过（软删）：平台侧仍在关注 → 复活该行，并保持用户原来的开关。
            if sub.deleted_at is not None:
                sub.deleted_at = None
            if not sub.title:
                sub.title = str(item.get("author_name") or author_id)
        # 顺带补一次标题（昵称可能改过）
        if item.get("author_name"):
            sub.title = str(item["author_name"])
        # 博主资料写 creator_profile（与关系状态分表，各存一份）
        upsert_creator(
            db,
            account.platform,
            author_id,
            author_name=str(item.get("author_name") or ""),
            avatar=str(item.get("avatar") or ""),
            bio=str(item.get("signature") or ""),
            follower_count=int(item.get("follower_count") or 0),
            verified=bool(item.get("verified", False)),
            homepage_url=str(item.get("home_url") or ""),
            raw=item.get("raw") or {},
        )

    # 不再回收已取关的人：刷新只负责"新增 + 更新"，**绝不动用户设置的同步开关**。
    # 原因：平台关注列表可能因为分页/风控只返回一部分，若据此判定"取关了"并关闭同步，
    # 会把用户明明开着的同步误关掉。要停止跟踪某个人，请显式「移除」。
    db.commit()
    return {"count": count, "cached": from_cache, "cooling": cooling}


class ToggleIn(BaseModel):
    sync_enabled: bool | None = None
    full_sync: bool | None = None
    # 二级开关：是否同步该博主名下视频（与"关注关系"分开，默认关闭）。
    sync_videos: bool | None = None


def _get_creator_sub(fc_id: int, db: Session) -> Subscription:
    sub = db.get(Subscription, fc_id)
    if sub is None or sub.source_type != "creator" or sub.deleted_at is not None:
        raise HTTPException(status_code=404, detail="关注博主不存在")
    return sub


@router.put("/{fc_id}", response_model=schemas.FollowedCreatorOut)
def update_followed_creator(fc_id: int, body: ToggleIn, db: Session = Depends(get_db)):
    """改同步开关。直接写 subscription —— 前端两个入口读写的是同一行，不可能漂移。"""
    sub = _get_creator_sub(fc_id, db)
    if body.sync_enabled is not None:
        sub.enabled = body.sync_enabled
    if body.sync_videos is not None:
        sub.sync_videos = body.sync_videos
        # 打开"同步博主视频"必然要先跟踪这段关注关系，否则同步会被直接拒绝。
        if body.sync_videos:
            sub.enabled = True
    if body.full_sync is not None:
        sub.only_new = not body.full_sync
    db.commit()
    db.refresh(sub)
    profiles = _profile_map(db, [sub])
    return _out(sub, profiles.get((sub.platform, sub.remote_id)))


@router.post("/{fc_id}/sync", status_code=202, response_model=schemas.JobRefOut)
async def sync_now(fc_id: int, db: Session = Depends(get_db)):
    """立即同步该博主：先确保同步开启，再提交一个同步任务。"""
    sub = _get_creator_sub(fc_id, db)
    # 立即同步等于是用户显式要这个博主的视频：把两个开关都打开，再跑一次。
    sub.enabled = True
    sub.sync_videos = True
    db.commit()
    db.refresh(sub)
    try:
        job = enqueue_sync(db, sub.id, force=True)
    except SyncCommandError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job.id, "status": "queued"}


@router.delete("/{fc_id}", response_model=schemas.UnfollowOut)
def unfollow(fc_id: int, db: Session = Depends(get_db)):
    """取消关注：软删该 creator 订阅，并停用其来源关系（已入库素材一律保留）。

    与 `DELETE /api/subscriptions/{id}` 保持同一保留策略（设计 v0.4「删得稳」）：
    取消关注/删订阅/删账号都只停用来源，绝不物理删除素材或内容。
    """
    from app.models import ContentSource

    sub = _get_creator_sub(fc_id, db)
    sources = db.scalars(
        select(ContentSource).where(ContentSource.subscription_id == sub.id)
    ).all()
    for source in sources:
        source.active = False
    sub.enabled = False
    sub.deleted_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "deactivated_sources": len(sources), "kept_contents": len(sources)}
