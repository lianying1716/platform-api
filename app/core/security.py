"""单管理员认证。

- 管理员密码哈希存 system_setting（PBKDF2-SHA256），首次启动必须先设置密码。
- 登录成功签发随机 session token，存内存（单进程单管理员足够），有效期 7 天。
"""

import hashlib
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import SystemSetting

_PBKDF2_ITERATIONS = 200_000
_TOKEN_TTL = 7 * 24 * 3600
_sessions: dict[str, float] = {}  # token -> 过期时间戳

ADMIN_PASSWORD_KEY = "admin_password_hash"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _PBKDF2_ITERATIONS)
    return secrets.compare_digest(dk.hex(), dk_hex)


def get_admin_hash(db: Session) -> str | None:
    row = db.get(SystemSetting, ADMIN_PASSWORD_KEY)
    return row.value if row else None


def set_admin_hash(db: Session, password: str) -> None:
    row = db.get(SystemSetting, ADMIN_PASSWORD_KEY)
    if row is None:
        row = SystemSetting(key=ADMIN_PASSWORD_KEY)
        db.add(row)
    row.value = hash_password(password)
    db.commit()


def issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + _TOKEN_TTL
    return token


def revoke_token(token: str) -> None:
    _sessions.pop(token, None)


def require_admin(request: Request, db: Session = Depends(get_db)) -> str:
    """FastAPI 依赖：校验 Authorization: Bearer <token>。"""
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    expires = _sessions.get(token)
    if not token or expires is None or expires < time.time():
        _sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return "admin"
