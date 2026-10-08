from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.config import settings
from app.core import security
from app.core.crypto import decrypt_credential, encrypt_credential, mask
from app.db import get_db
from app.models import AccountCredential, CredentialAuditLog, PlatformAccount
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
            if result.external_id and not account.external_id:
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

    # 登录成功 → 决定"更新哪个账号 / 要不要新建"，顺序如下：
    #   1. 有身份标识：按 (platform, external_id) 精确匹配
    #   2. 没匹配到，但该平台只有一个账号：复用它（并回填这次拿到的身份）
    #      —— 覆盖"上次没拿到身份、这次拿到了"的升级场景，以及平台不回身份的情况
    #   3. 该平台有多个账号却无法确定身份：报错，绝不猜着建重复号
    qr_sessions.remove(session_id)
    account = None
    if poll.external_id:
        account = db.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == sess.platform,
                PlatformAccount.external_id == poll.external_id,
            )
        )
    if account is None:
        existing = db.scalars(
            select(PlatformAccount)
            .where(PlatformAccount.platform == sess.platform)
            .order_by(PlatformAccount.id)
        ).all()
        if len(existing) == 1:
            account = existing[0]
        elif len(existing) > 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{adapter.label} 已有多个账号，且平台未返回可唯一识别的身份，"
                    "无法判断该更新哪一个。请手动在账号中心处理，或先删除多余账号后重试。"
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
        if poll.external_id and not account.external_id:
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
    accounts = db.scalars(select(PlatformAccount).order_by(PlatformAccount.id)).all()
    return [_account_out(a) for a in accounts]


@router.post("/accounts", response_model=schemas.AccountOut)
async def create_account(body: schemas.AccountCreate, db: Session = Depends(get_db)):
    adapter = _adapter_or_400(body.platform)
    try:
        plain = adapter.parse_manual_input(body.credential)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    account = PlatformAccount(
        platform=body.platform,
        display_name=body.display_name,
        external_id=str(body.credential.get("sec_user_id") or body.credential.get("dedeuserid") or ""),
        credential_kind=adapter.credential_kind,
    )
    db.add(account)
    db.flush()
    _save_credential(db, account, plain)
    _audit(db, account.id, "create", "手动录入")
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
    _audit(db, account.id, "delete", f"删除账号 {account.platform}/{account.display_name}")
    db.delete(account)
    db.commit()
    return {"ok": True}
