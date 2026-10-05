"""单篇文章导入编排：解析结果 -> ContentItem + MediaAsset + Transcript。

设计说明：文章没有音视频，复用平台的"文本本体"存储 —— 正文写入 Transcript.full_text，
这样 AI 分析（handle_analyze 读 transcript）与导出（/api/assets/export）零改动即支持文章。
download_status 置 done 表示"无需下载"（文章的抓取就是它自己的下载）。
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import render_subdir
from app.models import ContentItem, MediaAsset, Transcript
from app.modules import article_import

logger = logging.getLogger("platform.import_service")


async def import_wechat_article(db: Session, url: str) -> tuple[MediaAsset, bool]:
    """导入单篇公众号文章，返回 (MediaAsset, 是否新建)。幂等：同 external_id 复用。"""
    item = await article_import.fetch_article(url)

    content = db.scalar(
        select(ContentItem).where(
            ContentItem.platform == "wechat_mp",
            ContentItem.external_id == item["external_id"],
        )
    )
    if content is not None:
        asset = db.scalar(select(MediaAsset).where(MediaAsset.content_id == content.id))
        if asset is not None:
            return asset, False

    if content is None:
        content = ContentItem(
            platform="wechat_mp",
            external_id=item["external_id"],
            content_type="article",
            title=item["title"],
            author_name=item["author_name"],
            author_id=item["author_id"],
            url=item["url"],
            cover_url=item["cover_url"],
            published_at=item["published_at"],
            source_kind="import",
            source_id=None,
            extra={"html": item["html"]},
        )
        db.add(content)
        db.flush()

    asset = MediaAsset(
        content_id=content.id,
        relative_path=render_subdir(
            "{platform}/{creator}/{date}/{content_id}",
            platform="wechat_mp",
            creator=item["author_name"],
            date=item["published_at"] or content.created_at,
            content_id=item["external_id"],
        ),
        download_status="done",  # 文章的抓取即下载
        transcribe_status="done",  # 正文已在 transcript，无需转录
        analyze_status="pending",
    )
    db.add(asset)
    db.flush()

    db.add(
        Transcript(
            asset_id=asset.id,
            language="zh",
            model="wechat_article",
            segments=[],
            full_text=item["text"],
        )
    )
    db.commit()
    db.refresh(asset)
    logger.info("文章已入库 asset_id=%s title=%s", asset.id, item["title"])
    return asset, True
