from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import schemas
from app.config import settings
from app.core import security
from app.core.crypto import decrypt_credential, encrypt_credential, mask
from app.db import get_db
from app.models import AccountCredential, ContentItem, CredentialAuditLog, PlatformAccount
from app.modules import REGISTRY, get_adapter
from app.modules.base import PlatformAdapter
from app.modules.browser_qr import BrowserQrUnavailable
from app.services import qr_sessions

router = APIRouter(prefix="/api", tags=["accounts"], dependencies=[Depends(security.require_admin)])

# 微信公众号登录态经验有效期（wechat-article-exporter 的 KV TTL 为 4 天）
_WECHAT_TTL = timedelta(days=4)


def _audit(db: Session, account_id: int, action: str, detail: str = "", actor: str = "admin") -> None:
    db.add(CredentialAuditLog(account_id=account_id, action=action, actor=actor, detail=detail[:500]))


def _adapter_or_400(platform: str) -> PlatformAdapter:
    try:
        return get_adapter(platform)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _account_out(account: PlatformAccount) -> schemas.AccountOut:
    out = schemas.AccountOut.model_validate(account)
    adapter = REGISTRY.get(account.platform)
    out.platform_label = adapter.label if adapter else account.platform
    if account.credential:
        try:
            plain = decrypt_credential(account.credential.ciphertext)
            out.credential_preview = {k: mask(str(v)) for k, v in plain.items()}
        except Exception:
            out.credential_preview = {"error": "凭证解密失败（密钥文件是否变更？）"}
    return out


async def _run_check(db: Session, account: PlatformAccount, actor: str = "admin") -> schemas.CheckResponse:
    adapter = _adapter_or_400(account.platform)
    if account.credential is None:
        account.status, account.status_message = "error", "未录入凭证"
    else:
        plain = decrypt_credential(account.credential.ciphertext)
        try:
            result = await adapter.check(plain)
        except Exception as exc:  # 网络异常等不应把账号判死
            account.status, account.status_message = "error", f"体检失败: {exc}"
        else:
            account.status = "active" if result.ok else "expired"
            account.status_message = result.message
            if result.ok and getattr(result, "need_refresh", False) and account.platform == "bilibili":
                refresh = getattr(adapter, "refresh", None)
                if refresh is not None:
                    try:
                        refreshed = await refresh(plain)
                        _save_credential(db, account, refreshed)
                        account.status_message = "在线（已自动刷新 Cookie）"
                    except Exception as exc:  # noqa: BLE001 刷新失败不判死，留待人工重登
                        account.status_message = f"在线，但 Cookie 自动刷新失败: {exc}"
            if result.external_id:
                conflict = db.scalar(
                    select(PlatformAccount).where(
                        PlatformAccount.platform == account.platform,
                        PlatformAccount.external_id == result.external_id,
                        PlatformAccount.id != account.id,
                        PlatformAccount.is_system.is_(False),
                    )
                )
                if conflict is not None:
                    # 身份冲突不是“体检失败”，但也绝不能静默覆盖成别人的 ID。
                    account.status, account.status_message = "error", (
                        f"体检身份 {result.external_id} 已被账号 #{conflict.id} 占用，未自动覆盖"
                    )
                    account.last_checked_at = datetime.utcnow()
                    _audit(db, account.id, "check", account.status_message, actor)
                    db.commit()
                    raise HTTPException(status_code=409, detail=account.status_message)
                account.external_id = result.external_id
            if result.display_name and not account.display_name:
                account.display_name = result.display_name
    account.last_checked_at = datetime.utcnow()
    _audit(db, account.id, "check", account.status_message, actor)
    db.commit()
    return schemas.CheckResponse(ok=account.status == "active", status=account.status, message=account.status_message)


def _save_credential(db: Session, account: PlatformAccount, plain: dict) -> None:
    ciphertext = encrypt_credential(plain)
    if account.credential is None:
        account.credential = AccountCredential(ciphertext=ciphertext)
    else:
        account.credential.ciphertext = ciphertext
    if account.platform == "wechat_mp":
        account.expires_at = datetime.utcnow() + _WECHAT_TTL


# ---------- 平台元信息 ----------
@router.get("/platforms", response_model=list[schemas.PlatformInfo])
def list_platforms():
    return [
        schemas.PlatformInfo(
            platform=a.platform,
            label=a.label,
            credential_kind=a.credential_kind,
            supports_qr=a.supports_qr,
            credential_fields=a.credential_fields,
        )
        for a in REGISTRY.values()
    ]


# ---------- 扫码登录（必须先于 /accounts/{id} 声明） ----------
@router.post("/accounts/qr-login", response_model=schemas.QrLoginSession)
async def qr_login_create(body: schemas.QrLoginCreate):
    adapter = _adapter_or_400(body.platform)
    if not adapter.supports_qr:
        raise HTTPException(status_code=400, detail=f"{adapter.label} 暂不支持扫码登录，请手动粘贴 Cookie")
    try:
        start = await adapter.qr_start()
    except BrowserQrUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"发起扫码失败: {exc}")
    session_id = qr_sessions.create(body.platform, start.session_data, body.display_name)
    return schemas.QrLoginSession(
        session_id=session_id,
        platform=body.platform,
        qr_image_base64=start.qr_image_b64,
        expires_in=settings.qr_session_ttl,
    )


@router.get("/accounts/qr-login/{session_id}", response_model=schemas.QrLoginStatus)
async def qr_login_poll(session_id: str, db: Session = Depends(get_db)):
    sess = qr_sessions.get(session_id)
    if sess is None:
        return schemas.QrLoginStatus(status="expired", message="会话不存在或已过期，请重新发起")
    adapter = get_adapter(sess.platform)
    poll = await adapter.qr_poll(sess.data)
    if poll.status != "success":
        if poll.status in ("expired", "error"):
            qr_sessions.remove(session_id)
        return schemas.QrLoginStatus(status=poll.status, message=poll.message)

    # 登录成功 → 决定"更新哪个账号 / 要不要新建"：
    #   1. 有身份标识：按 (platform, external_id) 精确匹配并更新；匹配不到则新建
    #   2. 无身份标识：仅当该平台没有任何真实账号时允许创建首个无身份账号；
    #      已有任何真实账号则 409，绝不靠“只有一个”覆盖或猜身份
    qr_sessions.remove(session_id)
    account = None
    if poll.external_id:
        account = db.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == sess.platform,
                PlatformAccount.external_id == poll.external_id,
                PlatformAccount.is_system.is_(False),
            )
        )
    if account is None and not poll.external_id:
        existing = db.scalars(
            select(PlatformAccount)
            .where(
                PlatformAccount.platform == sess.platform,
                PlatformAccount.is_system.is_(False),
            )
            .order_by(PlatformAccount.id)
        ).all()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{adapter.label} 已有账号，且本次登录未返回可唯一识别的身份，"
                    "无法安全判断归属。请手动录入或在账号中心处理。"
                ),
            )
    if account is None:
        account = PlatformAccount(
            platform=sess.platform,
            display_name=sess.display_name or poll.display_name,
            external_id=poll.external_id,
            credential_kind=adapter.credential_kind,
        )
        db.add(account)
        db.flush()
        _audit(db, account.id, "create", "扫码登录创建")
    else:
        # 补上后拿到的身份/昵称，让账号卡片显示得更完整
        if poll.external_id:
            account.external_id = poll.external_id
        if poll.display_name and not sess.display_name:
            account.display_name = poll.display_name
        _audit(db, account.id, "update", "扫码登录更新凭证")
    _save_credential(db, account, poll.credential or {})
    account.status, account.status_message = "active", poll.message or "登录成功"
    account.last_checked_at = datetime.utcnow()
    db.commit()
    return schemas.QrLoginStatus(status="success", message=poll.message, account_id=account.id)


# ---------- 账号 CRUD ----------
@router.get("/accounts", response_model=list[schemas.AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    accounts = db.scalars(
        select(PlatformAccount)
        .where(
            PlatformAccount.is_system.is_(False),
            PlatformAccount.status != "deleted",
        )
        .order_by(PlatformAccount.id)
    ).all()
    return [_account_out(a) for a in accounts]


@router.post("/accounts", response_model=schemas.AccountOut)
async def create_account(body: schemas.AccountCreate, db: Session = Depends(get_db)):
    adapter = _adapter_or_400(body.platform)
    try:
        plain = adapter.parse_manual_input(body.credential)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    external_id = str(
        body.credential.get("sec_user_id")
        or body.credential.get("dedeuserid")
        or body.credential.get("user_id")
        or ""
    )
    account = None
    if external_id:
        account = db.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == body.platform,
                PlatformAccount.external_id == external_id,
                PlatformAccount.is_system.is_(False),
            )
        )
    if account is None:
        account = PlatformAccount(
            platform=body.platform,
            display_name=body.display_name,
            external_id=external_id,
            credential_kind=adapter.credential_kind,
        )
        db.add(account)
        try:
            db.flush()
        except IntegrityError:
            # 并发下另一个请求已抢先创建同一身份：回退为更新该账号。
            db.rollback()
            account = db.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.platform == body.platform,
                    PlatformAccount.external_id == external_id,
                    PlatformAccount.is_system.is_(False),
                )
            )
            if account is None:
                raise HTTPException(status_code=409, detail="账号身份冲突，请重试")
        audit_action = "create"
        audit_detail = "手动录入"
    else:
        if body.display_name:
            account.display_name = body.display_name
        audit_action = "update"
        audit_detail = "手动录入去重更新"
    _save_credential(db, account, plain)
    _audit(db, account.id, audit_action, audit_detail)
    db.commit()
    if not body.skip_check:
        await _run_check(db, account)
    db.refresh(account)
    return _account_out(account)


@router.get("/accounts/{account_id}", response_model=schemas.AccountOut)
def get_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _account_out(account)


@router.put("/accounts/{account_id}/credential", response_model=schemas.AccountOut)
async def update_credential(account_id: int, body: schemas.CredentialUpdate, db: Session = Depends(get_db)):
    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    adapter = _adapter_or_400(account.platform)
    try:
        plain = adapter.parse_manual_input(body.credential)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _save_credential(db, account, plain)
    account.status, account.status_message = "unchecked", ""
    _audit(db, account.id, "update", "手动更新凭证")
    db.commit()
    if not body.skip_check:
        await _run_check(db, account)
    db.refresh(account)
    return _account_out(account)


@router.post("/accounts/{account_id}/check", response_model=schemas.CheckResponse)
async def check_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return await _run_check(db, account)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.is_system:
        raise HTTPException(status_code=400, detail="系统账号不能删除")
    content_count = db.scalar(
        select(func.count(ContentItem.id)).where(ContentItem.account_id == account.id)
    ) or 0
    _audit(
        db,
        account.id,
        "delete",
        f"软删除账号 {account.platform}/{account.display_name}；保留素材 {content_count} 条",
    )
    account.status = "deleted"
    account.status_message = "账号已停用，素材保留"
    db.commit()
    return {"ok": True, "soft_deleted": True, "kept_contents": content_count}
