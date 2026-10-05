"""素材库 API（P2/P3/P5/P6 共用）。

- 列表：全维度筛选（平台/作者/状态/来源）。
- 详情：元数据 + 文件位置 + 转录 + 分析。
- 入队：转录 / 分析。
- 批量导出：md / json。
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.queue import enqueue
from app.core.storage import resolve_asset_dir
from app.db import get_db
from app.models import (
    AnalysisResult,
    ContentItem,
    ContentMetricSnapshot,
    MediaAsset,
    Transcript,
)

router = APIRouter(prefix="/api", tags=["assets"], dependencies=[Depends(security.require_admin)])


class ExportRequest(BaseModel):
    asset_ids: list[int]
    format: str = "md"  # md / json


def _latest_metric(db: Session, content_id: int) -> ContentMetricSnapshot | None:
    return db.scalar(
        select(ContentMetricSnapshot)
        .where(ContentMetricSnapshot.content_id == content_id)
        .order_by(ContentMetricSnapshot.captured_at.desc())
        .limit(1)
    )


@router.get("/assets")
def list_assets(
    db: Session = Depends(get_db),
    platform: str | None = None,
    author_id: str | None = None,
    download_status: str | None = None,
    transcribe_status: str | None = None,
    source_kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    stmt = select(MediaAsset, ContentItem).join(ContentItem, MediaAsset.content_id == ContentItem.id)
    if platform:
        stmt = stmt.where(ContentItem.platform == platform)
    if author_id:
        stmt = stmt.where(ContentItem.author_id == author_id)
    if source_kind:
        stmt = stmt.where(ContentItem.source_kind == source_kind)
    if download_status:
        stmt = stmt.where(MediaAsset.download_status == download_status)
    if transcribe_status:
        stmt = stmt.where(MediaAsset.transcribe_status == transcribe_status)
    stmt = stmt.order_by(MediaAsset.id.desc()).limit(min(limit, 200)).offset(offset)
    rows = db.execute(stmt).all()
    out = []
    for asset, content in rows:
        metric = _latest_metric(db, content.id)
        out.append(
            {
                "asset_id": asset.id,
                "content_id": content.id,
                "platform": content.platform,
                "title": content.title,
                "author_name": content.author_name,
                "cover_url": content.cover_url,
                "published_at": content.published_at.isoformat() if content.published_at else None,
                "download_status": asset.download_status,
                "transcribe_status": asset.transcribe_status,
                "analyze_status": asset.analyze_status,
                "metrics": {
                    "play": metric.play if metric else 0,
                    "like": metric.like if metric else 0,
                    "comment": metric.comment if metric else 0,
                }
                if metric
                else None,
            }
        )
    return out


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    content = db.get(ContentItem, asset.content_id)
    transcript = db.scalar(select(Transcript).where(Transcript.asset_id == asset_id))
    analysis = db.scalar(select(AnalysisResult).where(AnalysisResult.asset_id == asset_id))
    metric = _latest_metric(db, content.id) if content else None
    return {
        "asset_id": asset.id,
        "content": {
            "id": content.id,
            "platform": content.platform,
            "title": content.title,
            "author_name": content.author_name,
            "author_id": content.author_id,
            "url": content.url,
            "published_at": content.published_at.isoformat() if content.published_at else None,
        }
        if content
        else None,
        "file": {
            "relative_path": asset.relative_path,
            "video_file": asset.video_file,
            "audio_file": asset.audio_file,
            "full_dir": str(resolve_asset_dir(db, asset)) if asset.relative_path else "",
        },
        "status": {
            "download": asset.download_status,
            "transcribe": asset.transcribe_status,
            "analyze": asset.analyze_status,
            "error": asset.error,
        },
        "metrics": {
            "play": metric.play if metric else 0,
            "like": metric.like if metric else 0,
            "comment": metric.comment if metric else 0,
            "favorite": metric.favorite if metric else 0,
            "share": metric.share if metric else 0,
        }
        if metric
        else None,
        "transcript": {
            "language": transcript.language,
            "model": transcript.model,
            "segments": transcript.segments,
            "full_text": transcript.full_text,
        }
        if transcript
        else None,
        "analysis": {
            "summary": analysis.summary,
            "tags": analysis.tags,
            "structured": analysis.structured,
            "model": analysis.model,
        }
        if analysis
        else None,
    }


@router.post("/assets/{asset_id}/transcribe")
def enqueue_transcribe(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    asset.transcribe_status = "pending"
    db.commit()
    job = enqueue(db, "transcribe", {"asset_id": asset_id})
    return {"job_id": job.id}


@router.post("/assets/{asset_id}/analyze")
def enqueue_analyze(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    asset.analyze_status = "pending"
    db.commit()
    job = enqueue(db, "analyze", {"asset_id": asset_id})
    return {"job_id": job.id}


@router.post("/assets/{asset_id}/download")
def enqueue_download(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    content = db.get(ContentItem, asset.content_id)
    asset.download_status = "pending"
    db.commit()
    job = enqueue(db, "download", {"asset_id": asset_id, "url": content.url if content else ""})
    return {"job_id": job.id}


@router.post("/assets/export")
def export_assets(body: ExportRequest, db: Session = Depends(get_db)):
    docs = []
    for asset_id in body.asset_ids:
        asset = db.get(MediaAsset, asset_id)
        if asset is None:
            continue
        content = db.get(ContentItem, asset.content_id)
        transcript = db.scalar(select(Transcript).where(Transcript.asset_id == asset_id))
        analysis = db.scalar(select(AnalysisResult).where(AnalysisResult.asset_id == asset_id))
        docs.append(
            {
                "title": content.title if content else "",
                "platform": content.platform if content else "",
                "author": content.author_name if content else "",
                "url": content.url if content else "",
                "transcript": transcript.full_text if transcript else "",
                "summary": analysis.summary if analysis else "",
                "tags": analysis.tags if analysis else [],
            }
        )
    if body.format == "json":
        return JSONResponse(content=docs)
    # markdown
    parts = []
    for d in docs:
        parts.append(
            f"# {d['title']}\n\n"
            f"- 平台: {d['platform']}\n- 作者: {d['author']}\n- 链接: {d['url']}\n\n"
            f"## 摘要\n\n{d['summary']}\n\n"
            f"## 标签\n\n{', '.join(d['tags'])}\n\n"
            f"## 转录全文\n\n{d['transcript']}\n"
        )
    md = "\n\n---\n\n".join(parts)
    return JSONResponse(content={"format": "md", "content": md})
