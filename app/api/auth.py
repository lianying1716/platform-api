from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app import schemas
from app.core import security
from app.db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 会话 Cookie 有效期与 token 一致（7 天）。httpOnly 防脚本读取，
# SameSite=Lax 兼容局域网直接访问；HTTP 部署下不能设 Secure，否则 Cookie 不生效。
_COOKIE_MAX_AGE = 7 * 24 * 3600


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        security.SESSION_COOKIE,
        token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )


@router.get("/status", response_model=schemas.AuthStatus)
def auth_status(db: Session = Depends(get_db)):
    return schemas.AuthStatus(initialized=security.get_admin_hash(db) is not None)


@router.post("/setup", response_model=schemas.TokenResponse)
def setup(body: schemas.SetupRequest, response: Response, db: Session = Depends(get_db)):
    if security.get_admin_hash(db) is not None:
        raise HTTPException(status_code=400, detail="管理员密码已设置，请直接登录")
    if not security.initialize_admin_hash(db, body.password):
        raise HTTPException(status_code=400, detail="管理员密码已设置，请直接登录")
    token = security.issue_token(db)
    _set_session_cookie(response, token)
    return schemas.TokenResponse(token=token)


@router.post("/login", response_model=schemas.TokenResponse)
def login(
    body: schemas.LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = security.consume_login_attempt(db, client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="登录尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
    stored = security.get_admin_hash(db)
    if stored is None:
        raise HTTPException(status_code=400, detail="尚未初始化，请先设置管理员密码")
    if not security.verify_password(body.password, stored):
        raise HTTPException(status_code=401, detail="密码错误")
    security.clear_login_budget(db, client_ip)
    token = security.issue_token(db)
    _set_session_cookie(response, token)
    return schemas.TokenResponse(token=token)


@router.post("/logout", response_model=schemas.OkOut)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip() or request.cookies.get(security.SESSION_COOKIE, "")
    security.revoke_token(db, token)
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/change-password", response_model=schemas.OkOut)
def change_password(
    body: schemas.ChangePasswordRequest,
    response: Response,
    db: Session = Depends(get_db),
    _: str = Depends(security.require_admin),
):
    stored = security.get_admin_hash(db)
    if stored is None or not security.verify_password(body.old_password, stored):
        raise HTTPException(status_code=401, detail="原密码错误")
    security.set_admin_hash(db, body.new_password)
    security.bump_session_epoch(db)
    # The password-change response itself must not leave its caller with a
    # token from the old epoch.
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"ok": True}
