"""单管理员认证与会话边界。

- 管理员密码哈希存 system_setting（PBKDF2-SHA256），首次启动必须先设置密码。
- 登录成功签发随机 session token，有效期 7 天；token 只以 SHA-256 摘要落库，
  因此 API 重启、多个进程和反向代理后的多个 worker 看到的是同一份会话状态。
- token 绑定数据库中的非秘密 session epoch；改密/登出立即撤销。
- 登录失败预算与 API Key 限流一样落库，避免重启或多副本绕过。
"""

import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AdminSession, AuthRateEvent, SystemSetting

_PBKDF2_ITERATIONS = 200_000
_TOKEN_TTL = 7 * 24 * 3600
_LOGIN_WINDOW_SECONDS = 5 * 60
_LOGIN_PER_IP_LIMIT = 5
_LOGIN_GLOBAL_LIMIT = 25

ADMIN_PASSWORD_KEY = "admin_password_hash"
SESSION_EPOCH_KEY = "session_epoch"
# 会话 Cookie 名；视频等标签无法带 Authorization 头，靠它鉴权。
SESSION_COOKIE = "xsai_session"
_ARTICLE_IMAGE_KEY = "article-image-proxy-v1"
_SQLITE_LOGIN_LOCK = threading.Lock()


def sign_article_image(asset_id: int, index: int) -> str:
    """Short-lived-enough capability for one rendered article image.

    The signature binds the asset and source index, so the proxy cannot be used
    as an arbitrary URL fetcher even by a logged-in browser.
    """
    payload = f"{int(asset_id)}:{int(index)}".encode()
    digest = hmac.new(_ARTICLE_IMAGE_KEY.encode(), payload, hashlib.sha256).hexdigest()
    return digest[:32]


def valid_article_image_signature(asset_id: int, index: int, value: str) -> bool:
    return hmac.compare_digest(sign_article_image(asset_id, index), value or "")


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


def initialize_admin_hash(db: Session, password: str) -> bool:
    """Atomically claim the single-admin slot.

    Checking ``get_admin_hash`` and then writing is racy: two first-run requests
    can both observe an empty table.  The primary key on ``SystemSetting.key``
    is the actual serialization point; only the request that inserts it wins.
    """
    from sqlalchemy.exc import IntegrityError

    db.add(SystemSetting(key=ADMIN_PASSWORD_KEY, value=hash_password(password)))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _epoch(db: Session) -> int:
    row = db.get(SystemSetting, SESSION_EPOCH_KEY)
    try:
        return int(row.value) if row is not None else 0
    except (TypeError, ValueError):
        return 0


def utcnow() -> datetime:
    return datetime.utcnow()


def _session_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def _login_cutoff(now: datetime) -> datetime:
    return now - timedelta(seconds=_LOGIN_WINDOW_SECONDS)


def _login_keys(client_ip: str) -> tuple[str, str]:
    ip_key = "ip:" + hashlib.sha256((client_ip or "unknown").encode()).hexdigest()
    return ip_key, "global"


def _lock_login_keys(db: Session, keys: list[str]) -> None:
    """Serialize count-and-insert across API processes.

    PostgreSQL gets transaction-scoped advisory locks, released automatically at
    commit.  SQLite is only supported for development/tests; a process lock keeps
    threaded tests honest and does not pretend to provide multi-host atomicity.
    """
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy import text

        # Fixed lock namespace derived from the key.  Sorting makes the lock
        # order deterministic, avoiding deadlocks between IP and global locks.
        for key in sorted(keys):
            token = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF
            db.execute(text("SELECT pg_advisory_xact_lock(:token)"), {"token": token})


def _count_events(db: Session, key: str, cutoff: datetime) -> int:
    from sqlalchemy import func, select

    return int(
        db.scalar(
            select(func.count(AuthRateEvent.id)).where(
                AuthRateEvent.key == key,
                AuthRateEvent.created_at > cutoff,
            )
        )
        or 0
    )


def _oldest_event_at(db: Session, key: str, cutoff: datetime) -> datetime | None:
    from sqlalchemy import select

    return db.scalar(
        select(AuthRateEvent.created_at)
        .where(AuthRateEvent.key == key, AuthRateEvent.created_at > cutoff)
        .order_by(AuthRateEvent.created_at)
        .limit(1)
    )


def _consume_sliding_attempts(
    db: Session,
    keys_and_limits: list[tuple[str, int]],
    *,
    now: datetime,
) -> tuple[bool, int]:
    from sqlalchemy import delete

    cutoff = _login_cutoff(now)
    keys = [key for key, _limit in keys_and_limits]
    _lock_login_keys(db, keys)

    # Prune expired rows inside the same transaction.  This keeps the table
    # bounded even if the worker janitor is temporarily stopped.
    db.execute(
        delete(AuthRateEvent).where(
            AuthRateEvent.key.in_(keys),
            AuthRateEvent.created_at <= cutoff,
        )
    )
    blocked: list[tuple[datetime, int]] = []
    for key, limit in keys_and_limits:
        if _count_events(db, key, cutoff) >= limit:
            oldest = _oldest_event_at(db, key, cutoff)
            if oldest is not None:
                retry = int((oldest + timedelta(seconds=_LOGIN_WINDOW_SECONDS) - now).total_seconds())
                blocked.append((oldest, max(1, retry)))
    if blocked:
        db.rollback()
        return False, max(retry for _oldest, retry in blocked)

    db.add_all([AuthRateEvent(key=key, created_at=now) for key, _limit in keys_and_limits])
    db.commit()
    return True, 0


def consume_login_attempt(db: Session, client_ip: str, *, now: datetime | None = None) -> tuple[bool, int]:
    """Reserve a login attempt for one IP and the deployment-wide budget.

    The IP bucket prevents one host from burning the global budget; the global
    bucket prevents an attacker from rotating addresses forever.  A successful
    login clears only the IP bucket, so a legitimate retry is not punished by
    its own earlier typos while distributed guessing remains bounded.
    """
    current = now or utcnow()
    ip_key, global_key = _login_keys(client_ip)
    if db.get_bind().dialect.name != "postgresql":
        with _SQLITE_LOGIN_LOCK:
            return _consume_sliding_attempts(
                db,
                [(ip_key, _LOGIN_PER_IP_LIMIT), (global_key, _LOGIN_GLOBAL_LIMIT)],
                now=current,
            )
    return _consume_sliding_attempts(
        db,
        [(ip_key, _LOGIN_PER_IP_LIMIT), (global_key, _LOGIN_GLOBAL_LIMIT)],
        now=current,
    )


def login_allowed(db: Session, client_ip: str, *, now: datetime | None = None) -> tuple[bool, int]:
    """Read-only view used by tests/diagnostics; login consumes via the call above."""
    current = now or utcnow()
    cutoff = _login_cutoff(current)
    ip_key, global_key = _login_keys(client_ip)
    ip_count = _count_events(db, ip_key, cutoff)
    global_count = _count_events(db, global_key, cutoff)
    if ip_count < _LOGIN_PER_IP_LIMIT and global_count < _LOGIN_GLOBAL_LIMIT:
        return True, 0
    oldest = _oldest_event_at(db, ip_key if ip_count >= _LOGIN_PER_IP_LIMIT else global_key, cutoff)
    retry = 1
    if oldest is not None:
        retry = max(1, int((oldest + timedelta(seconds=_LOGIN_WINDOW_SECONDS) - current).total_seconds()))
    return False, retry


def clear_login_budget(db: Session, client_ip: str, *, now: datetime | None = None) -> None:
    """Clear the per-IP bucket after a successful login."""
    from sqlalchemy import delete

    ip_key, _global_key = _login_keys(client_ip)
    db.execute(delete(AuthRateEvent).where(AuthRateEvent.key == ip_key))
    db.commit()


def issue_token(db: Session | None = None) -> str:
    if db is None:
        from app.db import SessionLocal

        with SessionLocal() as local:
            return issue_token(local)
    token = f"{_epoch(db)}.{secrets.token_urlsafe(32)}"
    now = utcnow()
    db.add(
        AdminSession(
            token_hash=_session_hash(token),
            expires_at=now + timedelta(seconds=_TOKEN_TTL),
            created_at=now,
        )
    )
    db.commit()
    return token


def revoke_token(db: Session | None = None, token: str = "") -> None:
    if db is None:
        from app.db import SessionLocal

        with SessionLocal() as local:
            revoke_token(local, token)
        return
    row = db.get(AdminSession, _session_hash(token))
    if row is not None:
        row.revoked_at = utcnow()
        db.commit()


def bump_session_epoch(db: Session | None = None) -> None:
    """Invalidate every token issued in the previous epoch.

    The epoch is persisted in ``SystemSetting`` so another API process sees the
    invalidation immediately, without storing any credential material there.
    """
    if db is None:
        from app.db import SessionLocal

        with SessionLocal() as local:
            bump_session_epoch(local)
        return
    row = db.get(SystemSetting, SESSION_EPOCH_KEY)
    epoch = _epoch(db) + 1
    if row is None:
        row = SystemSetting(key=SESSION_EPOCH_KEY)
        db.add(row)
    row.value = str(epoch)
    db.commit()
    from sqlalchemy import delete

    db.execute(delete(AdminSession))
    db.commit()


def token_valid(token: str, db: Session | None = None) -> bool:
    """Validate a persisted session, shared by every API process."""
    if not token:
        return False
    if db is None:
        from app.db import SessionLocal

        with SessionLocal() as local:
            return token_valid(token, local)
    prefix, _, _secret = token.partition(".")
    try:
        issued_epoch = int(prefix)
    except ValueError:
        return False
    if issued_epoch != _epoch(db):
        return False
    row = db.get(AdminSession, _session_hash(token))
    if row is None or row.revoked_at is not None or row.expires_at < utcnow():
        return False
    return True


def require_admin(request: Request, db: Session = Depends(get_db)) -> str:
    """FastAPI 依赖：校验 Authorization: Bearer <token>，或会话 Cookie。

    接受 Cookie 是为了让 ``<video src>`` 这类无法自定义请求头的标签也能鉴权
    （登录时同时下发 httpOnly Cookie）。
    """
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        token = request.cookies.get(SESSION_COOKIE, "")
    if not token_valid(token, db):
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return "admin"
