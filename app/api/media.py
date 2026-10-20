"""Authenticated same-origin image proxy for remote avatars and article images."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core import media_cache, security
from app.core.archive_avatar import (
    ArchiveAvatarInvalid,
    archive_avatar_name,
    resolve_archive_avatar,
)
from app.core.egress import (
    EgressBlocked,
    EgressContentTypeBlocked,
    fetch_image,
    validate_image_bytes,
)
from app.db import get_db
from app.models import CreatorProfile

router = APIRouter(prefix="/api/media", tags=["media"], dependencies=[Depends(security.require_admin)])


@router.get("/avatars/{profile_id}")
async def get_avatar(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(CreatorProfile, profile_id)
    if profile is None or not profile.avatar:
        raise HTTPException(status_code=404, detail="头像不存在")
    try:
        archive_name = archive_avatar_name(profile.avatar)
    except ArchiveAvatarInvalid as exc:
        raise HTTPException(status_code=422, detail=f"头像地址非法: {exc}") from exc
    if archive_name is not None:
        try:
            path = resolve_archive_avatar(archive_name, settings.data_dir)
        except ArchiveAvatarInvalid as exc:
            raise HTTPException(status_code=422, detail=f"头像地址非法: {exc}") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="头像不存在")
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="头像不存在") from exc
        try:
            content_type = validate_image_bytes(body, None, allow_missing_type=True)
        except EgressContentTypeBlocked as exc:
            raise HTTPException(status_code=422, detail=f"头像内容不是图片: {exc}") from exc
        return StreamingResponse(
            iter([body]),
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=86400"},
        )
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
