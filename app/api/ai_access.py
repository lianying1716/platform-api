"""AI 接入管理 API（P4）：管理员侧。

- API Key 创建/列表/吊销（明文仅创建时返回一次）。
- 调用日志查询（审计）。
对外被 AI 调用的高层能力在 api/skills.py。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import api_auth, security
from app.db import get_db
from app.models import ApiCallLog, ApiKey

router = APIRouter(prefix="/api/ai", tags=["ai-access"], dependencies=[Depends(security.require_admin)])

_VALID_SCOPES = {"read", "operate", "admin"}


class KeyCreate(BaseModel):
    name: str
    scopes: list[str] = ["read", "operate"]
    rate_limit_per_min: int = 60


@router.get("/keys")
def list_keys(db: Session = Depends(get_db)):
    keys = db.scalars(select(ApiKey).order_by(ApiKey.id.desc())).all()
    return [
        {
            "id": k.id,
            "name": k.name,
            "prefix": k.prefix,
            "scopes": k.scopes,
            "rate_limit_per_min": k.rate_limit_per_min,
            "enabled": k.enabled,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat(),
        }
        for k in keys
    ]


@router.post("/keys")
def create_key(body: KeyCreate, db: Session = Depends(get_db)):
    bad = set(body.scopes) - _VALID_SCOPES
    if bad:
        raise HTTPException(status_code=422, detail=f"非法 scope: {', '.join(bad)}")
    raw, key_hash, prefix = api_auth.generate_key()
    key = ApiKey(
        name=body.name,
        key_hash=key_hash,
        prefix=prefix,
        scopes=body.scopes,
        rate_limit_per_min=body.rate_limit_per_min,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    # 明文仅此一次返回
    return {"id": key.id, "name": key.name, "api_key": raw, "scopes": key.scopes}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: int, db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Key 不存在")
    key.enabled = False
    db.commit()
    return {"ok": True}


@router.get("/call-logs")
def call_logs(db: Session = Depends(get_db), limit: int = 100, offset: int = 0):
    logs = db.scalars(
        select(ApiCallLog).order_by(ApiCallLog.id.desc()).limit(min(limit, 500)).offset(offset)
    ).all()
    return [
        {
            "id": x.id,
            "key_id": x.key_id,
            "tool": x.tool,
            "params_summary": x.params_summary,
            "status": x.status,
            "duration_ms": x.duration_ms,
            "created_at": x.created_at.isoformat(),
        }
        for x in logs
    ]
