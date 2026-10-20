"""API Key 鉴权（P4）：外部 AI / HTTP 客户端用。

- Key 形如 sk-<random>，只存 sha256 哈希，明文仅创建时返回一次。
- scope 控权：read（查询）/ operate（建任务/登录）/ admin（设置）。
- 每次调用落 api_call_log，可审计、可吊销。
- 限流：数据库按 key 每分钟计数，重启与多进程共享同一窗口。
"""

import hashlib
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiKey
from app.core import security

_PREFIX = "sk-"


def generate_key() -> tuple[str, str, str]:
    """返回 (明文key, 哈希, 展示前缀)。"""
    raw = _PREFIX + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:10]
    return raw, key_hash, prefix


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _minute_start(now=None):
    from datetime import datetime

    current = now or datetime.utcnow()
    return current.replace(second=0, microsecond=0)


def _check_rate(key: ApiKey, db: Session) -> bool:
    """Atomically consume one slot in the current minute.

    The conditional UPSERT is the concurrency boundary: two API workers or a
    restart can no longer both "forget" the window.  Rows are tiny and are
    pruned by the worker janitor.
    """
    return consume_rate_slot(db, key.id, key.rate_limit_per_min)


def consume_rate_slot(db: Session, key_id: int, limit: int, *, now=None) -> bool:
    """Consume one slot for ``key_id`` in ``now``'s minute.

    Exposed separately so restart and multi-process behavior can be tested
    without fabricating HTTP requests.
    """
    minute = _minute_start(now)
    # PostgreSQL and SQLite share this UPSERT shape.
    stmt = text(
        """
        INSERT INTO api_rate_window (key_id, window_start, count, updated_at)
        VALUES (:key_id, :window_start, 1, :now)
        ON CONFLICT (key_id, window_start) DO UPDATE
        SET count = api_rate_window.count + 1, updated_at = excluded.updated_at
        WHERE api_rate_window.count < :limit
        """
    )
    from datetime import datetime

    result = db.execute(
        stmt,
        {
            "key_id": key_id,
            "window_start": minute,
            "limit": limit,
            "now": now or datetime.utcnow(),
        },
    )
    db.commit()
    return (result.rowcount or 0) > 0


def authenticate(request: Request, db: Session) -> ApiKey:
    auth = request.headers.get("Authorization", "")
    raw = auth.removeprefix("Bearer ").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="缺少 API Key")
    key = db.query(ApiKey).filter(ApiKey.key_hash == _hash(raw), ApiKey.enabled.is_(True)).first()
    if key is None:
        raise HTTPException(status_code=401, detail="API Key 无效或已禁用")
    if not _check_rate(key, db):
        raise HTTPException(status_code=429, detail="超出调用频率限制")
    return key


def require_scope(*needed: str):
    """依赖工厂：要求 key 具备指定 scope 之一。"""

    def dep(request: Request, db: Session = Depends(get_db)) -> ApiKey:
        key = authenticate(request, db)
        if needed and not any(s in key.scopes for s in needed):
            raise HTTPException(status_code=403, detail=f"权限不足，需要 scope: {', '.join(needed)}")
        from datetime import datetime

        key.last_used_at = datetime.utcnow()
        db.commit()
        return key

    return dep


def require_admin_or_scope(*needed: str):
    """管理员 session 或 API Key scope 任一满足即可的依赖工厂。

    注意：多个 FastAPI ``Depends`` 是 AND 关系，不能把 require_admin 与
    require_scope 同时挂在路由上。
    """

    def dep(request: Request, db: Session = Depends(get_db)):
        raw = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if raw.startswith(_PREFIX):
            key = (
                db.query(ApiKey)
                .filter(ApiKey.key_hash == _hash(raw), ApiKey.enabled.is_(True))
                .first()
            )
            if key is None:
                raise HTTPException(status_code=401, detail="API Key 无效或已禁用")
            if not _check_rate(key, db):
                raise HTTPException(status_code=429, detail="超出调用频率限制")
            if needed and not any(scope in key.scopes for scope in needed):
                raise HTTPException(status_code=403, detail=f"权限不足，需要 scope: {', '.join(needed)}")
            from datetime import datetime

            key.last_used_at = datetime.utcnow()
            db.commit()
            return key

        # 非 sk- 的 Bearer 视为管理员 session；管理员 Cookie 只在这里兜底，
        # 对已识别的 API Key 通道永远不构成冲突。
        token = raw or request.cookies.get(security.SESSION_COOKIE, "")
        if not security.token_valid(token, db):
            raise HTTPException(status_code=401, detail="未登录或会话已过期")
        return "admin"

    return dep
