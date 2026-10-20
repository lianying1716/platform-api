"""订阅同步 API（P2）。

- 列某账号的远端源（收藏夹/关注/合集），需有效凭证。
- 订阅 CRUD：勾选要跟踪的源。
- 手动触发同步 / 查看同步历史。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.crypto import decrypt_credential
from app.db import get_db
from app.models import ContentSource, CreatorProfile, PlatformAccount, StorageRoot, Subscription, SyncRun
from app.modules import get_adapter
from app.application.commands.sync import SyncCommandError, enqueue_sync
from app.domain.capabilities import ALL_QUALITIES, require_quality, require_source_type

router = APIRouter(prefix="/api", tags=["subscriptions"], dependencies=[Depends(security.require_admin)])


class SubscriptionIn(BaseModel):
    account_id: int
    source_type: str
    remote_id: str
    title: str = ""
    # 是否开启同步。收藏/点赞/合集默认关闭，需要用户手动打开（对齐抖小云的独立开关）；
    # 关注列表（creator）默认开启「关系跟踪」，但博主视频另有 sync_videos 二级开关。
    enabled: bool = True
    sync_videos: bool = False
    auto_download: bool = False
    # None means inherit; do not use 360 as a fake "unset" value.
    interval_minutes: int | None = Field(None, ge=10)
    subdir_template: str = "{platform}/{account}/{creator}/{date}"
    # 订阅级存储根覆盖：None = 继承账号绑定（再回退默认根）。
    storage_root_id: int | None = None
    # 同步设置（A1/A4）：范围校验在 create/update 两处同时生效（同一个模型）。
    min_play: int = Field(0, ge=0)
    publish_after_days: int = Field(0, ge=0)
    max_items: int = Field(50, ge=1, le=500)
    only_new: bool = True
    download_quality: str = "best"
    comment_mode: str = "gate"
    # 订阅级自动加工开关（显式 opt-in；绕过热度门控直接入队）。
    auto_transcribe: bool | None = None
    auto_analyze: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _translate_legacy_comment_bool(cls, data):
        if isinstance(data, dict) and "with_comments" in data and "comment_mode" not in data:
            data = dict(data)
            data["comment_mode"] = "force" if data.pop("with_comments") else "gate"
        return data

    @field_validator("download_quality")
    @classmethod
    def _check_download_quality(cls, value: str) -> str:
        allowed = set(ALL_QUALITIES)
        if value not in allowed:
            raise ValueError(f"download_quality 必须是 {sorted(allowed)} 之一")
        return value

    @field_validator("comment_mode")
    @classmethod
    def _check_comment_mode(cls, value: str) -> str:
        if value not in ("off", "gate", "force"):
            raise ValueError("comment_mode 必须是 off/gate/force 之一")
        return value


class SubscriptionPatch(BaseModel):
    """Partial subscription update.

    PATCH exists so a switch can change exactly one field without rebuilding the
    whole form.  ``None`` is meaningful for nullable fields (inherit storage or
    processing defaults), so route handlers must use ``model_fields_set`` rather
    than truthiness when applying changes.
    """

    account_id: int | None = None
    source_type: str | None = None
    remote_id: str | None = None
    title: str | None = None
    enabled: bool | None = None
    sync_videos: bool | None = None
    auto_download: bool | None = None
    interval_minutes: int | None = Field(None, ge=10)
    subdir_template: str | None = None
    storage_root_id: int | None = None
    min_play: int | None = Field(None, ge=0)
    publish_after_days: int | None = Field(None, ge=0)
    max_items: int | None = Field(None, ge=1, le=500)
    only_new: bool | None = None
    download_quality: str | None = None
    comment_mode: str | None = None
    auto_transcribe: bool | None = None
    auto_analyze: bool | None = None

    model_config = {"extra": "forbid"}

    @field_validator("download_quality")
    @classmethod
    def _check_download_quality(cls, value: str | None) -> str | None:
        if value is not None and value not in ALL_QUALITIES:
            raise ValueError(f"download_quality 必须是 {sorted(ALL_QUALITIES)} 之一")
        return value

    @field_validator("comment_mode")
    @classmethod
    def _check_comment_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in ("off", "gate", "force"):
            raise ValueError("comment_mode 必须是 off/gate/force 之一")
        return value


class SubscriptionOut(BaseModel):
    id: int
    account_id: int
    platform: str
    source_type: str
    remote_id: str
    title: str
    enabled: bool
    sync_videos: bool = False
    auto_download: bool
    interval_minutes: int | None = None
    subdir_template: str = "{platform}/{account}/{creator}/{date}"
    storage_root_id: int | None = None
    min_play: int = 0
    publish_after_days: int = 0
    max_items: int = 50
    only_new: bool = True
    download_quality: str = "best"
    comment_mode: str = "gate"
    auto_transcribe: bool | None = None
    auto_analyze: bool | None = None
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
    try:
        require_source_type(account.platform, source_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    try:
        require_source_type(account.platform, body.source_type)
        require_quality(account.platform, body.download_quality)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    data = body.model_dump()
    # Legacy client compatibility: accept the old boolean and translate it to
    # the canonical mode.  New clients should send comment_mode directly.
    if "enabled" not in body.model_fields_set:
        # 未显式指定时：只有「关注列表」默认开启关系跟踪，收藏/点赞等默认关闭。
        data["enabled"] = body.source_type == "creator"
    sub = Subscription(platform=account.platform, **data)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


_NON_NULL_PATCH_FIELDS = {
    "account_id", "source_type", "remote_id", "title", "enabled", "sync_videos",
    "auto_download", "subdir_template", "min_play", "publish_after_days",
    "max_items", "only_new", "download_quality", "comment_mode",
}


def _apply_subscription_changes(sub: Subscription, changes: dict, db: Session) -> None:
    """Validate and apply only explicitly supplied fields to one subscription."""
    for key in _NON_NULL_PATCH_FIELDS & changes.keys():
        if changes[key] is None:
            raise HTTPException(status_code=422, detail=f"{key} 不能为 null")

    source_type = changes.get("source_type", sub.source_type)
    quality = changes.get("download_quality", sub.download_quality)
    try:
        require_source_type(sub.platform, source_type)
        require_quality(sub.platform, quality)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if "account_id" in changes and changes["account_id"] != sub.account_id:
        account = db.get(PlatformAccount, changes["account_id"])
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        if account.is_system or account.status != "active":
            raise HTTPException(status_code=422, detail="账号已停用、系统账号或不可用，无法绑定订阅")
        if account.platform != sub.platform:
            raise HTTPException(status_code=422, detail="订阅不能转移到其他平台账号")

    if "storage_root_id" in changes and changes["storage_root_id"] is not None:
        if db.get(StorageRoot, changes["storage_root_id"]) is None:
            raise HTTPException(status_code=404, detail="存储根目录不存在")

    for key, value in changes.items():
        setattr(sub, key, value)


@router.put("/subscriptions/{sub_id}", response_model=SubscriptionOut)
def update_subscription(sub_id: int, body: SubscriptionIn, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if sub is None or sub.deleted_at is not None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    _apply_subscription_changes(sub, body.model_dump(exclude_unset=True), db)
    db.commit()
    db.refresh(sub)
    return sub


@router.patch("/subscriptions/{sub_id}", response_model=SubscriptionOut)
def patch_subscription(sub_id: int, body: SubscriptionPatch, db: Session = Depends(get_db)):
    """Update only the named fields.

    This is the canonical endpoint for toggles.  A PATCH with ``{"enabled":
    false}`` must never reset a custom storage root, a tri-state processing
    override, or any capability setting that was not sent.
    """
    sub = db.get(Subscription, sub_id)
    if sub is None or sub.deleted_at is not None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="至少需要提供一个要修改的字段")
    _apply_subscription_changes(sub, changes, db)
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


@router.post("/subscriptions/{sub_id}/sync", status_code=202)
async def trigger_sync(sub_id: int, db: Session = Depends(get_db)):
    try:
        job = enqueue_sync(db, sub_id, force=True)
    except SyncCommandError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job.id, "status": "queued"}


@router.get("/subscriptions/{sub_id}/runs")
def sync_runs(sub_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(SyncRun).where(SyncRun.subscription_id == sub_id).order_by(SyncRun.id.desc()).limit(50)
    ).all()
    return [
        {
            "id": r.id,
            "job_id": r.job_id,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "heartbeat_at": r.heartbeat_at.isoformat() if r.heartbeat_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status,
            "fetched": r.fetched,
            "added": r.added,
            "updated": r.updated,
            "error": r.error,
        }
        for r in rows
    ]


@router.get("/subscriptions/{sub_id}/runs/{run_id}")
def sync_run_detail(sub_id: int, run_id: int, db: Session = Depends(get_db)):
    run = db.scalar(
        select(SyncRun).where(SyncRun.id == run_id, SyncRun.subscription_id == sub_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="同步记录不存在")
    return {
        "id": run.id,
        "job_id": run.job_id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "fetched": run.fetched,
        "added": run.added,
        "updated": run.updated,
        "error": run.error,
    }
