"""API Key 鉴权（P4）：外部 AI / HTTP 客户端用。

- Key 形如 sk-<random>，只存 sha256 哈希，明文仅创建时返回一次。
- scope 控权：read（查询）/ operate（建任务/登录）/ admin（设置）。
- 每次调用落 api_call_log，可审计、可吊销。
- 限流：进程内简单滑窗，按 key 每分钟计数（单机够用）。
"""

import hashlib
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiKey

_PREFIX = "sk-"
# key_id -> [时间戳]
_rate_window: dict[int, list[float]] = {}


def generate_key() -> tuple[str, str, str]:
    """返回 (明文key, 哈希, 展示前缀)。"""
    raw = _PREFIX + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:10]
    return raw, key_hash, prefix


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _check_rate(key: ApiKey) -> bool:
    now = time.time()
    window = _rate_window.setdefault(key.id, [])
    cutoff = now - 60
    window[:] = [t for t in window if t > cutoff]
    if len(window) >= key.rate_limit_per_min:
        return False
    window.append(now)
    return True


def authenticate(request: Request, db: Session) -> ApiKey:
    auth = request.headers.get("Authorization", "")
    raw = auth.removeprefix("Bearer ").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="缺少 API Key")
    key = db.query(ApiKey).filter(ApiKey.key_hash == _hash(raw), ApiKey.enabled.is_(True)).first()
    if key is None:
        raise HTTPException(status_code=401, detail="API Key 无效或已禁用")
    if not _check_rate(key):
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
