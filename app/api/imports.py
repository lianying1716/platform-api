"""导入类 API：目前仅单篇公众号文章。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import security
from app.db import get_db
from app.models import ContentItem
from app.modules.article_import import ArticleImportError
from app.services.import_service import import_wechat_article
from app import schemas

router = APIRouter(prefix="/api/imports", tags=["imports"], dependencies=[Depends(security.require_admin)])


class ArticleImportIn(BaseModel):
    url: str


@router.post("/wechat-article", response_model=schemas.ArticleImportOut)
async def import_article(body: ArticleImportIn, db: Session = Depends(get_db)):
    """导入单篇微信公众号文章。重复导入同一篇返回既有素材。"""
    try:
        asset, created = await import_wechat_article(db, body.url)
    except ArticleImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    content = db.get(ContentItem, asset.content_id)
    return {
        "asset_id": asset.id,
        "content_id": asset.content_id,
        "title": content.title if content else "",
        "author_name": content.author_name if content else "",
        "already_existed": not created,
    }
