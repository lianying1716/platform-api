"""Authenticated same-origin image proxy for remote avatars and article images."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core import media_cache, security
from app.core.egress import EgressBlocked, EgressContentTypeBlocked, fetch_image
from app.db import get_db
from app.models import CreatorProfile

router = APIRouter(prefix="/api/media", tags=["media"], dependencies=[Depends(security.require_admin)])


@router.get("/avatars/{profile_id}")
async def get_avatar(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(CreatorProfile, profile_id)
    if profile is None or not profile.avatar:
        raise HTTPException(status_code=404, detail="头像不存在")
    cached = media_cache.read("avatar", profile_id, profile.avatar)
    if cached is not None:
        ctype, body = cached
        return StreamingResponse(iter([body]), media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})
    try:
        image = await fetch_image(profile.avatar, purpose="avatar", platform=profile.platform, timeout=15)
    except EgressContentTypeBlocked as exc:
        raise HTTPException(status_code=422, detail=f"头像内容不是图片: {exc}") from exc
    except EgressBlocked as exc:
        raise HTTPException(status_code=422, detail=f"头像地址不允许访问: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"头像获取失败: {exc}") from exc
    if image.status_code != 200 or not image.content:
        raise HTTPException(status_code=502, detail=f"头像获取失败：HTTP {image.status_code}")
    media_cache.write("avatar", profile_id, profile.avatar, content_type=image.content_type, body=image.content)
    return StreamingResponse(
        iter([image.content]),
        media_type=image.content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )
