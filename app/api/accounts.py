import logging
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
from app.models import (
    AccountCredential,
    ContentItem,
    CredentialAuditLog,
    PlatformAccount,
    StorageRoot,
)
from app.modules import REGISTRY, get_adapter
from app.modules.base import PlatformAdapter
from app.modules.browser_qr import BrowserQrUnavailable
from app.services import qr_sessions

router = APIRouter(prefix="/api", tags=["accounts"], dependencies=[Depends(security.require_admin)])

logger = logging.getLogger("platform.accounts.qr")

# 微信公众号登录态经验有效期（wechat-article-exporter 的 KV TTL 为 4 天）
_WECHAT_TTL = timedelta(days=4)


def _audit(db: Session, account_id: int, action: str, detail: str = "", actor: str = "admin") -> None:
    db.add(CredentialAuditLog(account_id=account_id, action=action, actor=actor, detail=detail[:500]))


def _adapter_or_400(platform: str) -> PlatformAdapter:
    try:
        return get_adapter(platform)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _account_out(account: PlatformAccount, root: StorageRoot | None = None) -> schemas.AccountOut:
    out = schemas.AccountOut.model_validate(account)
    adapter = REGISTRY.get(account.platform)
    out.platform_label = adapter.label if adapter else account.platform
    if root is not None:
        out.storage_root_name = root.name
        out.storage_root_path = root.path
    if account.credential:
        try:
            plain = decrypt_credential(account.credential.ciphertext)
            out.credential_preview = {k: mask(str(v)) for k, v in plain.items()}
        except Exception:
            out.credential_preview = {"error": "凭证解密失败（密钥文件是否变更？）"}
    return out


async def _verify_after_login(db: Session, account: PlatformAccount) -> bool:
    """扫码/更新凭证后**立刻**真去平台探一次活，把结果写进 account。

    返回是否通过（account.status == "active"）。

    为什么要这一步：扫码成功只说明"平台把登录态写进了浏览器"，不代表账号能用——
    Cookie 可能不完整、接口要签名、也可能立刻被风控。没有这一步，"导入成功"只是
    我们自己的想象，问题会推迟到用户真正同步时才爆出来（那时更难定位）。

    失败**不删账号**：多数是临时风控或凭证不全，用户可以在卡片上重试/重新扫码。
    但状态必须如实标异常，绝不冒充"在线"。
    """
    from app.services.account_health import check_account

    if account.credential is None:
        account.status, account.status_message = "error", "未获取到凭证，请重新扫码或手动录入"
        account.last_checked_at = datetime.utcnow()
        db.commit()
        _audit(db, account.id, "verify", account.status_message)
        db.commit()
        return False

    before = account.external_id
    try:
        await check_account(account)
    except Exception as exc:  # noqa: BLE001 探活本身炸了也要如实反映，不能泄成"在线"
        account.status = "error"
        account.status_message = f"登录后验证失败：{str(exc)[:200]}"
        account.last_checked_at = datetime.utcnow()
        db.commit()
        _audit(db, account.id, "verify", account.status_message)
        db.commit()
        return False

    ok = account.status == "active"
    if ok:
        # 探活通过才是真的"在线"——这条 status 有平台返回值背书，不是我们编的。
        account.status_message = f"在线（已通过真实验证）：{account.status_message or '探活成功'}"
    else:
        # 探活没通过：把原因说清楚，并指明下一步（重新扫码 / 手动配置）。
        reason = account.status_message or "平台未返回有效登录态"
        account.status = account.status if account.status in ("expired", "error") else "error"
        account.status_message = (
            f"{reason}。请重新扫码登录，或在「更新凭证」里手动粘贴 Cookie 后重试"
        )
    # 身份冲突保护：探活可能带回 external_id，若已被别的账号占用则不覆盖（与 _run_check 同口径）。
    if account.external_id and account.external_id != before:
        conflict = db.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == account.platform,
                PlatformAccount.external_id == account.external_id,
                PlatformAccount.id != account.id,
                PlatformAccount.is_system.is_(False),
            )
        )
        if conflict is not None:
            account.external_id = before
            account.status = "error"
            account.status_message = f"账号身份已被 #{conflict.id} 占用，未自动覆盖，请人工确认"
            ok = False
    db.commit()
    _audit(db, account.id, "verify", account.status_message)
    db.commit()
    return ok


async def _run_check(
    db: Session, account: PlatformAccount, actor: str = "admin", manual: bool = False
) -> schemas.CheckResponse:
    if account.status == "deleted":
        raise HTTPException(status_code=409, detail="账号已停用，不能体检；请重新登录或恢复账号")
    if not bool(account.sync_enabled) and not manual:
        # 沉默期：**自动**体检仍然禁止（不替用户产生平台调用）。
        # 但用户手动点「体检」要放行：那是他主动想验证账号，正是这次要修的场景——
        # 沉默期账号永远不体检，假"在线"就永远留在库里没人发现。
        from app.services.account_state import SILENT_MESSAGE

        raise HTTPException(status_code=409, detail=SILENT_MESSAGE)
    _adapter_or_400(account.platform)
    before = account.external_id
    from app.services.account_health import check_account

    plain_before = decrypt_credential(account.credential.ciphertext) if account.credential else {}
    await check_account(account)
    if account.external_id and account.external_id != before:
        conflict = db.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == account.platform,
                PlatformAccount.external_id == account.external_id,
                PlatformAccount.id != account.id,
                PlatformAccount.is_system.is_(False),
            )
        )
        if conflict is not None:
            account.external_id = before
            if account.credential is not None and plain_before:
                account.credential.ciphertext = encrypt_credential(plain_before)
            account.status, account.status_message = "error", (
                f"体检身份已被账号 #{conflict.id} 占用，未自动覆盖"
            )
            account.last_checked_at = datetime.utcnow()
            _audit(db, account.id, "check", account.status_message, actor)
            db.commit()
            raise HTTPException(status_code=409, detail=account.status_message)
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
    from app.domain.capabilities import as_public_manifest

    capabilities = {row["platform"]: row for row in as_public_manifest()}
    out = []
    for adapter in REGISTRY.values():
        cap = capabilities.get(adapter.platform) or {}
        out.append(schemas.PlatformInfo(
            platform=adapter.platform,
            label=adapter.label,
            credential_kind=adapter.credential_kind,
            supports_qr=adapter.supports_qr,
            credential_fields=adapter.credential_fields,
            source_types=cap.get("source_types", []),
            download_qualities=cap.get("download_qualities", []),
            keyword_crawl=bool(cap.get("keyword_crawl")),
            comments=bool(cap.get("comments")),
            follow_import=bool(cap.get("follow_import")),
            metrics=list(cap.get("metrics") or []),
        ))
    return out


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
    # 带上 account_id：这是「重新扫码」在覆盖已有账号，而不是新建。
    session_id = qr_sessions.create(
        body.platform, start.session_data, body.display_name, account_id=body.account_id
    )
    return schemas.QrLoginSession(
        session_id=session_id,
        platform=body.platform,
        qr_image_base64=start.qr_image_b64,
        expires_in=settings.qr_session_ttl,
        qr_image_expires_in=start.qr_image_expires_in,
    )


@router.get("/accounts/qr-login/{session_id}", response_model=schemas.QrLoginStatus)
async def qr_login_poll(session_id: str, db: Session = Depends(get_db)):
    # 已结束的会话：如实回放终态（成功/风控/确认超时…），而不是一律说"会话不存在"。
    # 前端每 3s 一次、失败时还可能并发，迟到的轮询撞上"已删除"很常见；回错原因会把
    # 真因盖掉，让用户和排查都往"二维码过期"这个与事实无关的方向走。
    terminal = qr_sessions.terminal_status(session_id)
    if terminal is not None:
        status, message, account_id = terminal
        # 带上原话：真机上"确认后界面没反应"到底是哪一步，全靠这条日志定性。
        logger.info(
            "qr_poll_replay session=%s status=%s account=%s msg=%s",
            session_id[:12], status, account_id, str(message)[:120],
        )
        return schemas.QrLoginStatus(status=status, message=message, account_id=account_id)
    sess = qr_sessions.get(session_id)
    if sess is None:
        # 走到这里 = 会话真的不存在（从未创建 / 已被 GC / 进程重启丢了内存态）。
        # 真机排查时这条最关键：它证明失败与"二维码过期"无关。
        logger.warning("qr_poll_unknown_session session=%s", session_id[:12])
        return schemas.QrLoginStatus(status="expired", message="会话不存在或已过期，请重新发起")
    adapter = get_adapter(sess.platform)
    poll = await adapter.qr_poll(sess.data)
    if poll.status != "success":
        # ⚠️ 不要在这里因 expired/error 删会话。抖音的二维码**每 60 秒必然过期一次**
        # 并由登录页自动换新码，适配层对这种情况已经自愈（抓新图回传、返回 pending）。
        # 只有「会话真的不存在」或「超过会话 TTL」才会返回 expired —— 那两种情况适配层
        # 自己已经收口。API 层再删一次，会把"正在换码"误判成"整次扫码失败"，
        # 表现为用户刚扫码就弹"二维码已过期"，而手机端其实已确认。
        if poll.status == "error":
            # error 是真正的失败（如风控拦截）：结束会话，但**记住原因**。
            qr_sessions.finish(session_id, poll.status, poll.message)
        return schemas.QrLoginStatus(
            status=poll.status,
            message=poll.message,
            qr_image_base64=poll.qr_image_b64,
            # 用 getattr：适配器可能返回旧结构的 QrPoll（无此字段），别因此 500。
            qr_image_expires_in=getattr(poll, "qr_image_expires_in", None),
        )

    # 登录成功 → 决定"更新哪个账号 / 要不要新建"：
    # 先占位，防止两个并发轮询同时走到这里各建一个账号；真正结果在落库后再记。
    if not qr_sessions.claim(session_id, "success", poll.message):
        # 已有并发的一轮在处理（或刚结束）：回放终态，绝不重复落库。
        status, message, account_id = qr_sessions.terminal_status(session_id) or (
            "success",
            poll.message,
            None,
        )
        return schemas.QrLoginStatus(status=status, message=message, account_id=account_id)
    #   0. 「重新扫码」带了 account_id：直接覆盖那一个，不猜身份
    #      （扫码失败过的账号往往没有 external_id，靠身份匹配必然 409，用户无法原地重试）
    #   1. 有身份标识：按 (platform, external_id) 精确匹配并更新；匹配不到则新建
    #   2. 无身份标识：仅当该平台没有任何真实账号时允许创建首个无身份账号；
    #      已有任何真实账号则 409，绝不靠“只有一个”覆盖或猜身份
    account = None
    if sess.account_id is not None:
        account = db.get(PlatformAccount, sess.account_id)
        if account is None or account.status == "deleted":
            account = None  # 目标账号没了/已停用：退回按身份处理
        elif account.platform != sess.platform:
            # 平台对不上就不敢覆盖，退回常规路径（防止前端传错 id）
            logger.warning(
                "qr_relogin_account_mismatch account=%s want=%s got=%s",
                sess.account_id, sess.platform, account.platform,
            )
            account = None
    if account is None and poll.external_id:
        account = db.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == sess.platform,
                PlatformAccount.external_id == poll.external_id,
                PlatformAccount.is_system.is_(False),
            )
        )
    # 注意：只有当**这次没有指定目标账号**、且平台没给身份、而库里确实已有该平台
    # 账号时才 409。若用户点了「重新扫码」并带了 account_id，即使这次没拿到身份，
    # 目标账号也已经确定，不该再拦（否则扫码失败过的账号永远无法原地重试）。
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
            detail = (
                f"{adapter.label} 已有账号，且本次登录未返回可唯一识别的身份，"
                "无法安全判断归属。请手动录入或在账号中心处理。"
            )
            # 会话到此为止（浏览器上下文适配层已关）；记下原因，免得后续轮询
            # 又回一句与事实无关的"会话不存在或已过期"。
            qr_sessions.finish(session_id, "error", detail)
            raise HTTPException(status_code=409, detail=detail)
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
    # ⚠️ 关键：扫码成功只代表"平台把登录态写进了浏览器"，**不等于账号能用**。
    # Cookie 可能不完整（抖音登录态分散在 sessionid/sid_tt/LOGIN_STATUS/localStorage，
    # 缺一项就调不通）、web 接口还要签名、也可能立刻被风控。
    # 旧实现直接写 status="active" + last_checked_at=now()，等于**凭空编了一个"在线"**，
    # 比手动粘 Cookie 那条路（会真去探活）还松——于是"导入成功"的账号可能在同步时才炸。
    # 这里改成：先落凭证，再**真去平台探一次活**，用真实结果决定状态。
    db.flush()
    account.status, account.status_message = "unchecked", "登录成功，正在验证账号可用性…"
    account.last_checked_at = None
    db.commit()
    verified = await _verify_after_login(db, account)
    if not verified:
        # 验证没通过：凭证保留（可能只是临时风控，用户可原地重试），
        # 但状态必须如实标异常，绝不冒充"在线"。
        qr_sessions.finish(session_id, "error", account.status_message)
        return schemas.QrLoginStatus(
            status="error",
            message=account.status_message,
            account_id=account.id,
        )
    # 成功也是终态：浏览器关闭后若前端还有一轮在途轮询回来，要回放"成功"，
    # 而不是让它撞上"会话不存在"、把界面从成功打回失败。
    qr_sessions.finish_success(session_id, account.id, account.status_message)
    return schemas.QrLoginStatus(
        status="success",
        message=account.status_message,
        account_id=account.id,
    )


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
    roots = {r.id: r for r in db.scalars(select(StorageRoot)).all()}
    return [_account_out(a, roots.get(a.storage_root_id)) for a in accounts]


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
    account.status, account.status_message = "unchecked", "凭证已更新，正在验证…"
    account.last_checked_at = None
    _audit(db, account.id, "update", "手动更新凭证")
    db.commit()
    if not body.skip_check:
        # 用户主动更新凭证 = 主动要验证，沉默期也放行（manual=True）。
        await _run_check(db, account, manual=True)
    db.refresh(account)
    return _account_out(account)


@router.put("/accounts/{account_id}/sync-enabled", response_model=schemas.AccountOut)
def update_account_sync_enabled(
    account_id: int, body: schemas.AccountSyncToggle, db: Session = Depends(get_db)
):
    """账号级同步总开关。关闭 = 沉默期：该账号所有同步/采集/体检一律禁止（源头拦截）。

    订阅与素材都保留不删；重新打开即恢复（订阅各自开关照旧生效）。
    """
    account = db.get(PlatformAccount, account_id)
    if account is None or account.status == "deleted":
        raise HTTPException(status_code=404, detail="账号不存在或已停用")
    account.sync_enabled = body.enabled
    _audit(db, account.id, "update", f"同步总开关 -> {'开启' if body.enabled else '关闭（沉默期）'}")
    db.commit()
    db.refresh(account)
    return _account_out(account)


@router.put("/accounts/{account_id}/storage", response_model=schemas.AccountOut)
def update_account_storage(
    account_id: int, body: schemas.AccountStorageUpdate, db: Session = Depends(get_db)
):
    """给账号指定存储根目录；storage_root_id=None 表示回到默认根。

    两个账户可以指向同一个根（共用目录），也可以各指一个（独立目录），两种都合法。
    """
    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    root = None
    if body.storage_root_id is not None:
        root = db.get(StorageRoot, body.storage_root_id)
        if root is None:
            raise HTTPException(status_code=404, detail="存储根目录不存在")
    account.storage_root_id = body.storage_root_id
    _audit(
        db,
        account.id,
        "update",
        f"存储根改为 {root.name if root else '默认目录'}",
    )
    db.commit()
    db.refresh(account)
    return _account_out(account, root)


@router.post("/accounts/{account_id}/check", response_model=schemas.CheckResponse)
async def check_account(account_id: int, db: Session = Depends(get_db)):
    """手动体检：**沉默期也放行**（用户主动要验证账号，不是我们替他发请求）。"""
    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return await _run_check(db, account, manual=True)


@router.delete("/accounts/{account_id}", response_model=schemas.AccountDeleteOut)
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
