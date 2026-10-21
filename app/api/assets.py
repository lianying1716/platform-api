"""素材库 API（P2/P3/P5/P6 共用）。

- 列表：全维度筛选（平台/作者/状态/来源）。
- 详情：元数据 + 文件位置 + 转录 + 分析。
- 入队：转录 / 分析。
- 批量导出：md / json。
"""


import json
import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import security
from app.core.archive_avatar import ArchiveAvatarInvalid, resolve_archive_avatar
from app.core.queue import enqueue
from app.core import storage
from app.core.egress import is_public_image_url
from app.db import get_db
from app import schemas
from app.models import (
    AnalysisResult,
    Comment,
    ContentItem,
    ContentMetricSnapshot,
    MediaAsset,
    Transcript,
    VideoUnderstanding,
    ViralBreakdown,
    StorageRoot,
)

router = APIRouter(prefix="/api", tags=["assets"], dependencies=[Depends(security.require_admin)])


def _ai_schedule_hold(db: Session) -> dict:
    """入队 AI 任务时，若当前不在运行时段，返回给前端的等待信息。"""
    from app.core.ai_schedule import ai_schedule_status, get_ai_schedule

    status = ai_schedule_status(get_ai_schedule(db))
    return {
        "ai_schedule_held": not status["is_open"],
        "ai_next_open_at": status.get("next_open_at"),
    }


class ExportRequest(BaseModel):
    asset_ids: list[int]
    format: str = "md"  # md / json

    @field_validator("asset_ids")
    @classmethod
    def _bounded_asset_ids(cls, value: list[int]) -> list[int]:
        if len(value) > 500:
            raise ValueError("单次最多导出 500 条素材")
        return value

    @field_validator("format")
    @classmethod
    def _known_format(cls, value: str) -> str:
        if value not in ("md", "json"):
            raise ValueError("format 必须是 md 或 json")
        return value


class ProcessRequest(BaseModel):
    tier: str  # basic（转录）/ advanced（视频理解）
    force_category: str | None = None
    use_cover: bool | None = None


class ReclassifyRequest(BaseModel):
    # 与 ProcessRequest 同样用 Pydantic 布尔归一（"false"/"0" -> False），避免裸 dict 的 bool() 误判。
    force_category: str | None = None
    use_cover: bool | None = None


class BreakdownRequest(BaseModel):
    # Pydantic 布尔归一（"false"/"0" -> False）；已 done 时 force=True 才重跑。
    force: bool = False


class AssetDeleteRequest(BaseModel):
    reason: str = ""


class BulkRefreshMetricsRequest(BaseModel):
    """Backfill content whose latest play value is unknown or absent."""

    account_id: int | None = None
    platform: str | None = None
    limit: int = Field(500, ge=1, le=2000)


_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}


def _guess_media_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _VIDEO_TYPES:
        return _VIDEO_TYPES[ext]
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _parse_range(header: str, file_size: int) -> tuple[int, int] | None:
    """解析 `Range: bytes=start-end`；不合法返回 None（忽略 Range 全量返回）。"""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes=") :].split(",")[0].strip()
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            # 后缀范围 bytes=-N
            length = int(end_s)
            if length <= 0:
                return None
            start = max(0, file_size - length)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
    except ValueError:
        return None
    if start > end or start >= file_size:
        return None
    return start, min(end, file_size - 1)


def _iter_file(path: str, start: int, end: int, chunk: int = 1024 * 256):
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            data = fh.read(min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def _range_file_response(path: str, request: Request, media_type: str):
    """支持 Range 的文件响应：视频可拖动进度条，不把整段视频读进内存。"""
    file_size = storage.file_size(path)
    range_header = request.headers.get("range")
    rng = _parse_range(range_header, file_size) if range_header else None
    if rng is None:
        return FileResponse(path, media_type=media_type, headers={"Accept-Ranges": "bytes"})
    start, end = rng
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    return StreamingResponse(
        _iter_file(path, start, end),
        status_code=206,
        media_type=media_type,
        headers=headers,
    )


def _latest_metric(db: Session, content_id: int) -> ContentMetricSnapshot | None:
    return db.scalar(
        select(ContentMetricSnapshot)
        .where(ContentMetricSnapshot.content_id == content_id)
        .order_by(ContentMetricSnapshot.captured_at.desc())
        .limit(1)
    )


def _latest_metrics(db: Session, content_ids: list[int]) -> dict[int, ContentMetricSnapshot]:
    """Latest snapshot per content in one query.

    A list page used to call ``_latest_metric`` once per row (up to 200 extra
    queries).  Keep the single-row helper for detail endpoints and use this
    batch loader for collections.
    """
    if not content_ids:
        return {}
    rows = db.scalars(
        select(ContentMetricSnapshot)
        .where(ContentMetricSnapshot.content_id.in_(content_ids))
        .order_by(
            ContentMetricSnapshot.content_id,
            ContentMetricSnapshot.captured_at.desc(),
            ContentMetricSnapshot.id.desc(),
        )
    ).all()
    latest: dict[int, ContentMetricSnapshot] = {}
    for row in rows:
        latest.setdefault(row.content_id, row)
    return latest


def _live_asset_or_404(db: Session, asset_id: int) -> MediaAsset:
    """Return a visible asset or raise 404; soft-deleted rows are not addressable."""
    from app.services.storage_lifecycle import get_live_asset

    asset = get_live_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    return asset


@router.get(
    "/assets",
    response_model=list[schemas.AssetCard],
    responses={200: {"headers": {"X-Total-Count": {"schema": {"type": "integer"}}}}},
)
def list_assets(
    response: Response,
    db: Session = Depends(get_db),
    platform: str | None = None,
    account_id: int | None = None,
    author_id: str | None = None,
    content_type: str | None = None,
    category: str | None = None,
    download_status: str | None = None,
    transcribe_status: str | None = None,
    source_kind: str | None = None,
    sort: str = Query("latest", pattern="^(latest|published_at|synced_at|like_count)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = select(MediaAsset, ContentItem).join(ContentItem, MediaAsset.content_id == ContentItem.id)
    stmt = stmt.where(MediaAsset.deleted_at.is_(None))
    if platform:
        stmt = stmt.where(ContentItem.platform == platform)
    if account_id is not None:
        stmt = stmt.where(ContentItem.account_id == account_id)
    if author_id:
        stmt = stmt.where(ContentItem.author_id == author_id)
    if content_type:
        stmt = stmt.where(ContentItem.content_type == content_type)
    if category:
        stmt = stmt.where(ContentItem.auto_category == category)
    if source_kind:
        stmt = stmt.where(ContentItem.source_kind == source_kind)
    if download_status:
        stmt = stmt.where(MediaAsset.download_status == download_status)
    if transcribe_status:
        stmt = stmt.where(MediaAsset.transcribe_status == transcribe_status)
    # 排序（全部倒序）：默认「最新」（本地入库顺序），另可选发布时间 / 同步时间 / 点赞数。
    if sort == "published_at":
        stmt = stmt.order_by(ContentItem.published_at.desc().nullslast(), MediaAsset.id.desc())
    elif sort == "synced_at":
        stmt = stmt.order_by(MediaAsset.updated_at.desc(), MediaAsset.id.desc())
    elif sort == "like_count":
        # 点赞数取该内容最新一条指标快照（每条内容存多行趋势），用相关子查询。
        latest_like = (
            select(ContentMetricSnapshot.like)
            .where(ContentMetricSnapshot.content_id == ContentItem.id)
            .order_by(ContentMetricSnapshot.captured_at.desc())
            .limit(1)
            .correlate(ContentItem)
            .scalar_subquery()
        )
        stmt = stmt.order_by(latest_like.desc().nullslast(), MediaAsset.id.desc())
    else:  # latest：本次入库顺序（默认）
        stmt = stmt.order_by(MediaAsset.id.desc())
    # Keep the array body for existing Skills/HTTP clients, and expose the true
    # filtered count in a header so "共 N 条" is not the current page length.
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    response.headers["X-Total-Count"] = str(total)
    stmt = stmt.limit(limit).offset(offset)
    rows = db.execute(stmt).all()

    # 作者头像/主页：统一走 creator_profile（关注博主资料也写在这里，只此一处）
    from app.models import CreatorProfile

    authors = {(c.platform, c.author_id) for _a, c in rows if c.author_id}
    author_meta: dict[tuple[str, str], dict] = {}
    if authors:
        for cp in db.scalars(
            select(CreatorProfile).where(
                CreatorProfile.author_id.in_([aid for _p, aid in authors])
            )
        ).all():
            author_meta.setdefault(
                (cp.platform, cp.author_id),
                {
                    "avatar": cp.avatar,
                    "avatar_url": f"/api/media/avatars/{cp.id}" if cp.avatar else "",
                    "home_url": cp.homepage_url,
                    "name": cp.author_name,
                },
            )

    out = []
    metrics = _latest_metrics(db, [content.id for _asset, content in rows])
    # File sizes are resolved from the per-root file index once, not by
    # resolving the storage root and stat-ing a file for every row.
    size_map = storage.sizes_for_assets(db, [asset for asset, _content in rows])
    all_roots = db.scalars(select(StorageRoot).order_by(StorageRoot.id)).all()
    roots = {root.id: root.path for root in all_roots}
    default_root = next((root.path for root in all_roots if root.is_default), None)
    for asset, content in rows:
        metric = metrics.get(content.id)
        root_path = roots.get(asset.storage_root_id) if asset.storage_root_id is not None else None
        if root_path is None:
            root_path = default_root
        try:
            video_path = (
                storage.resolve_asset_file_path(
                    storage.join_root(root_path, asset.relative_path or ""),
                    asset.video_file,
                    label="视频文件名",
                )
                if root_path and asset.video_file
                else ""
            )
        except ValueError:
            video_path = ""
        has_file = bool(video_path) and os.path.isfile(video_path)
        size_bytes = size_map.get(asset.id, 0)
        meta = author_meta.get((content.platform, content.author_id), {})
        out.append(
            {
                "asset_id": asset.id,
                "content_id": content.id,
                "platform": content.platform,
                "account_id": content.account_id,
                "content_type": content.content_type or "video",
                "title": content.title,
                "author_name": content.author_name,
                "author_id": content.author_id,
                "author_avatar": meta.get("avatar", ""),
                "author_avatar_url": meta.get("avatar_url", ""),
                "author_home_url": meta.get("home_url", ""),
                # 归档导入的素材没有 CDN cover_url，只有本地封面缓存；老前端
                # （v1.0.2 及以前）只看 cover_url 是否非空来决定渲不渲染 <img>，
                # 所以这里给个非空哨兵值让它照样渲染（真实地址由 /cover 代理按
                # asset_id 拼，与 cover_url 内容无关）。
                "cover_url": content.cover_url
                or (f"/api/assets/{asset.id}/cover" if _has_cached_cover(asset.id) else ""),
                # 新前端直接看这个：CDN 地址或本地缓存任一存在即为 true。
                # Only render <img> for a URL that passes the same SSRF policy
                # used by the proxy; otherwise the browser logs a failed 422
                # resource even though the UI would fall back to a placeholder.
                "cover_allowed": bool(content.cover_url) and is_public_image_url(content.cover_url),
                "has_cover": (bool(content.cover_url) and is_public_image_url(content.cover_url)) or _has_cached_cover(asset.id),
                "url": content.url,
                "published_at": content.published_at.isoformat() if content.published_at else None,
                # 同步时间＝本地入库时间（含落盘），与「发布时间」区分
                "created_at": content.created_at.isoformat() if content.created_at else None,
                "synced_at": asset.updated_at.isoformat() if asset.updated_at else None,
                "duration_sec": content.duration_sec or 0,
                "auto_category": content.auto_category or "",
                "category_confidence": content.category_confidence or 0,
                "source_kind": content.source_kind or "",
                "size_bytes": size_bytes,
                # 视频可播放＝本地文件确实存在；文章走正文接口
                "playable": has_file,
                "tier": asset.process_tier or "",
                "download_status": asset.download_status,
                # 失败原因：列表页也要能看到"为什么失败"，否则只有一个"失败"标签没法排查。
                "error": asset.error or "",
                "transcribe_status": asset.transcribe_status,
                "analyze_status": asset.analyze_status,
                "understand_status": asset.understand_status,
                "metrics": {
                    "play": metric.play if metric else None,
                    "like": metric.like if metric else None,
                    "comment": metric.comment if metric else None,
                    "favorite": metric.favorite if metric else None,
                    "share": metric.share if metric else None,
                }
                if metric
                else None,
            }
        )
    return out


@router.get("/assets/{asset_id}", response_model=schemas.AssetDetail)
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = _live_asset_or_404(db, asset_id)
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
            # 保留字段名以兼容已有客户端，但只返回相对的素材目录；API 永不返回服务器绝对路径。
            "full_dir": asset.relative_path,
        },
        "status": {
            "download": asset.download_status,
            "transcribe": asset.transcribe_status,
            "analyze": asset.analyze_status,
            "tier": asset.process_tier,
            "understand": asset.understand_status,
            "error": asset.error,
        },
        "metrics": {
            "play": metric.play if metric else None,
            "like": metric.like if metric else None,
            "comment": metric.comment if metric else None,
            "favorite": metric.favorite if metric else None,
            "share": metric.share if metric else None,
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
            "model": analysis.model,
        }
        if analysis
        else None,
    }


@router.get("/assets/{asset_id}/model-calls", response_model=list[schemas.AssetModelCallOut])
def get_asset_model_calls(asset_id: int, db: Session = Depends(get_db)):
    """素材详情里的模型调用审计：按 attempt_group 展示主备尝试。"""
    from app.models import ModelCallAttempt

    _live_asset_or_404(db, asset_id)
    rows = db.scalars(
        select(ModelCallAttempt)
        .where(ModelCallAttempt.asset_id == asset_id)
        .order_by(ModelCallAttempt.id.desc())
    ).all()
    return [
        {
            "id": row.id,
            "attempt_group_id": row.attempt_group_id,
            "work_type": row.work_type,
            "operation": row.operation,
            "role": row.role,
            "provider_name": row.provider_name,
            "model_id": row.model_id,
            "model_key": row.model_key,
            "status": row.status,
            "error_class": row.error_class,
            "error_message": row.error_message,
            "http_status": row.http_status,
            "duration_ms": row.duration_ms,
            "usage": row.usage or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
        for row in rows
    ]


@router.delete("/assets/{asset_id}", status_code=202, response_model=schemas.AssetDeleteOut)
def delete_asset(asset_id: int, body: AssetDeleteRequest | None = None, db: Session = Depends(get_db)):
    """管理员删除素材：先软删除，worker 清理文件后再物理删除。"""
    from app.services.storage_lifecycle import request_asset_deletion

    try:
        _asset, audit, job = request_asset_deletion(
            db,
            asset_id,
            actor="admin",
            reason=(body.reason if body else ""),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="素材不存在或已删除") from exc
    return {"ok": True, "asset_id": asset_id, "audit_id": audit.id, "job_id": job.id, "status": "pending"}


@router.get("/assets/{asset_id}/media")
def stream_media(asset_id: int, request: Request, db: Session = Depends(get_db)):
    """视频流式播放：支持 HTTP Range（拖动进度条），未下载则 404。

    只服务 storage_root 内的本机文件；不暴露任何绝对路径。
    鉴权走全局 require_admin，它同时接受 Authorization 头与会话 Cookie
    （``<video>`` 标签无法自定义请求头，只能靠 Cookie）。
    """
    asset = _live_asset_or_404(db, asset_id)
    path = storage.resolve_asset_file(db, asset)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="本地没有可播放的视频文件")
    media_type = _guess_media_type(path)
    resp = _range_file_response(path, request, media_type)
    # 私有内容，禁止代理缓存
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


def _cover_cache_dir():
    """封面缓存目录：落在 data_dir 下（不进 git，可随时重建）。"""
    from app.config import settings

    path = Path(settings.data_dir) / "cover_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _has_cached_cover(asset_id: int) -> bool:
    """本地封面缓存是否存在（只查 meta，不读图片）。

    归档导入的素材没有 CDN cover_url，封面只在本地缓存里；前端要据此决定是否渲染
    <img>，否则会把有封面的素材也画成占位符。
    """
    try:
        return (_cover_cache_dir() / f"{asset_id}.json").exists()
    except OSError:
        return False


def _read_cached_cover(asset_id: int) -> tuple[str, bytes] | None:
    meta = _cover_cache_dir() / f"{asset_id}.json"
    if not meta.exists():
        return None
    try:
        data = json.loads(meta.read_text("utf-8"))
        body = (_cover_cache_dir() / data["file"]).read_bytes()
        # Validate pre-existing cache entries too; older builds only trusted
        # the remote Content-Type and may have cached HTML/SVG bytes.
        from app.core.egress import EgressContentTypeBlocked, validate_image_bytes

        return validate_image_bytes(
            body, str(data.get("type") or ""), allow_missing_type=True
        ), body
    except (OSError, ValueError, KeyError, EgressContentTypeBlocked):
        return None


def _write_cached_cover(asset_id: int, ctype: str, body: bytes) -> None:
    """增强项：写缓存失败不影响本次响应。"""
    try:
        name = f"{asset_id}.bin"
        (_cover_cache_dir() / name).write_bytes(body)
        (_cover_cache_dir() / f"{asset_id}.json").write_text(
            json.dumps({"file": name, "type": ctype}), "utf-8"
        )
    except OSError:
        pass


def _read_article_image_cache(asset_id: int, index: int, source: str) -> tuple[str, bytes] | None:
    from app.core import media_cache

    return media_cache.read("article", asset_id, index, source)


def _write_article_image_cache(asset_id: int, index: int, source: str, ctype: str, body: bytes) -> None:
    from app.core import media_cache

    media_cache.write("article", asset_id, index, source, content_type=ctype, body=body)


@router.get("/assets/{asset_id}/cover")
async def proxy_cover(asset_id: int, db: Session = Depends(get_db)):
    """封面图代理：服务端带 Referer/UA 拉取后回传，避免前端直连被签名/防盗链卡住。

    抖音封面是**带 sign 的 CDN 地址会过期**，B站封面也有防盗链（缺 Referer 直接 403）。
    照抄抖小云把封面落到本地；web 场景下用服务端代理等效解决：浏览器只请求我们的
    /api/assets/{id}/cover，由服务端补 Referer 去取，过期/失效时也有明确回退。
    """
    from app.core.egress import EgressBlocked, EgressContentTypeBlocked, fetch_image

    asset = _live_asset_or_404(db, asset_id)
    content = db.get(ContentItem, asset.content_id)
    url = (content.cover_url if content else "") or ""
    # 本地缓存优先：导入的归档素材没有 CDN cover_url，但有本地海报缓存。
    cached = _read_cached_cover(asset_id)
    if cached is not None:
        ctype, body = cached
        return StreamingResponse(
            iter([body]),
            media_type=ctype,
            headers={"Cache-Control": "private, max-age=86400"},
        )
    if not url:
        raise HTTPException(status_code=404, detail="该素材没有封面")
    platform = content.platform if content else ""
    referer = "https://www.douyin.com/" if platform == "douyin" else "https://www.bilibili.com/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
    }
    # 说明：封面 CDN **不带 Cookie**（公开图片地址），不构成"账号被封"的风险，
    # 因此不走 API 限速桶（否则 5 秒/张会把素材页拖垮）。真正的风险是"每次翻页都
    # 重打一遍 CDN" —— 用本地缓存解决：每张封面只在首次请求时取一次。
    try:
        image = await fetch_image(url, purpose="cover", platform=platform, headers=headers, timeout=20)
    except EgressContentTypeBlocked as exc:
        raise HTTPException(status_code=422, detail=f"封面内容不是图片: {exc}") from exc
    except EgressBlocked as exc:
        raise HTTPException(status_code=422, detail=f"封面地址不允许访问: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"封面获取失败: {exc}") from exc
    if image.status_code != 200 or not image.content:
        raise HTTPException(status_code=502, detail=f"封面获取失败：HTTP {image.status_code}")
    ctype = image.content_type
    _write_cached_cover(asset_id, ctype, image.content)
    return StreamingResponse(
        iter([image.content]),
        media_type=ctype,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/assets/{asset_id}/article", response_model=schemas.AssetArticle)
def get_article(asset_id: int, db: Session = Depends(get_db)):
    """文章正文：公众号等文本内容的阅读视图数据源。"""
    from bs4 import BeautifulSoup

    from app.modules.article_import import rewrite_article_images, sanitize_article_html

    asset = _live_asset_or_404(db, asset_id)
    content = db.get(ContentItem, asset.content_id)
    transcript = db.scalar(select(Transcript).where(Transcript.asset_id == asset_id))
    extra = content.extra if content and isinstance(content.extra, dict) else {}
    raw_html = str(extra.get("html") or "")
    # 存量文章可能早于入库清洗；API 是所有客户端（页面、MCP、导出）的共同出口，
    # 不能只依赖前端 SafeHtml 兜底。
    soup = BeautifulSoup(raw_html, "html.parser") if raw_html else None
    safe_html = ""
    if soup is not None:
        sanitize_article_html(soup)
        rewrite_article_images(soup, asset_id=asset_id)
        safe_html = sanitize_article_html(soup)
    return {
        "asset_id": asset_id,
        "title": content.title if content else "",
        "author_name": content.author_name if content else "",
        "platform": content.platform if content else "",
        "url": content.url if content else "",
        "published_at": content.published_at.isoformat() if content and content.published_at else None,
        "cover_url": content.cover_url if content else "",
        "html": safe_html,
        "text": transcript.full_text if transcript else "",
    }


@router.get("/articles/{asset_id}/images/{index}")
async def get_article_image(asset_id: int, index: int, sig: str = "", db: Session = Depends(get_db)):
    """Serve one article image through the SSRF-safe egress boundary."""
    from bs4 import BeautifulSoup

    from app.core.egress import EgressBlocked, EgressContentTypeBlocked, fetch_image
    from app.modules.article_import import rewrite_article_images, sanitize_article_html

    asset = _live_asset_or_404(db, asset_id)
    content = db.get(ContentItem, asset.content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    extra = content.extra if isinstance(content.extra, dict) else {}
    source = ""
    soup = BeautifulSoup(str(extra.get("html") or ""), "html.parser")
    # Match the display pipeline exactly: unsafe attributes/tags are removed
    # before image indexes are assigned, so a malicious first image cannot shift
    # the signature of a later safe image.
    sanitize_article_html(soup)
    sources = rewrite_article_images(soup, asset_id=None)
    if 0 <= index < len(sources):
        source = sources[index]
    if not source or not security.valid_article_image_signature(asset_id, index, sig):
        raise HTTPException(status_code=404, detail="图片不存在")
    if source.startswith("/"):
        raise HTTPException(status_code=404, detail="图片不存在")
    cached = _read_article_image_cache(asset_id, index, source)
    if cached is not None:
        ctype, body = cached
        return StreamingResponse(iter([body]), media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})
    try:
        image = await fetch_image(source, purpose="article", platform=content.platform, timeout=20)
    except EgressContentTypeBlocked as exc:
        raise HTTPException(status_code=422, detail=f"文章图片内容不是图片: {exc}") from exc
    except EgressBlocked as exc:
        raise HTTPException(status_code=422, detail=f"文章图片地址不允许访问: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"文章图片获取失败: {exc}") from exc
    if image.status_code != 200 or not image.content:
        raise HTTPException(status_code=502, detail=f"文章图片获取失败：HTTP {image.status_code}")
    _write_article_image_cache(asset_id, index, source, image.content_type, image.content)
    return StreamingResponse(
        iter([image.content]),
        media_type=image.content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/assets/{asset_id}/comments", response_model=schemas.AssetCommentPage)
def list_comments(
    asset_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    asset = _live_asset_or_404(db, asset_id)
    base = select(Comment).where(Comment.content_id == asset.content_id)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(
        base.order_by(Comment.like_count.desc(), Comment.id).limit(limit).offset(offset)
    ).all()
    return {
        "total": int(total or 0),
        "comments": [
            {
                "comment_id": c.comment_id,
                "nickname": c.nickname,
                "avatar": c.avatar,
                "content": c.content,
                "like_count": c.like_count,
                "ip_location": c.ip_location,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ],
    }


@router.post("/assets/{asset_id}/transcribe", response_model=schemas.JobRefOut)
def enqueue_transcribe(asset_id: int, db: Session = Depends(get_db)):
    asset = _live_asset_or_404(db, asset_id)
    from app.services.workflow_engine import WorkflowInputError, register_workflow

    try:
        result = register_workflow(
            db, asset_id, ["transcribe"], force=["transcribe"], commit=True
        )
    except WorkflowInputError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    asset.transcribe_status = "pending"
    db.commit()
    return {"job_id": result.queued_jobs[0] if result.queued_jobs else None, "run_id": result.run.id}


@router.post("/assets/{asset_id}/analyze", response_model=schemas.JobRefOut)
def enqueue_analyze(asset_id: int, db: Session = Depends(get_db)):
    asset = _live_asset_or_404(db, asset_id)
    asset.analyze_status = "pending"
    db.commit()
    from app.services.workflow_engine import WorkflowInputError, register_workflow

    try:
        result = register_workflow(
            db, asset_id, ["analyze"], force=["analyze"], commit=True
        )
    except WorkflowInputError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job_id": result.queued_jobs[0] if result.queued_jobs else None,
        "run_id": result.run.id,
        **_ai_schedule_hold(db),
    }


@router.post("/assets/{asset_id}/download", response_model=schemas.JobRefOut)
def enqueue_download(asset_id: int, db: Session = Depends(get_db)):
    asset = _live_asset_or_404(db, asset_id)
    content = db.get(ContentItem, asset.content_id)
    quality = "best"
    if content is not None:
        from app.models import Subscription

        sub = (
            db.get(Subscription, content.source_id)
            if content.source_kind == "subscription" and content.source_id
            else None
        )
        if sub is not None:
            quality = sub.download_quality
    asset.download_status = "pending"
    db.commit()
    job = enqueue(
        db,
        "download",
        {"asset_id": asset_id, "url": content.url if content else "", "quality": quality},
    )
    return {"job_id": job.id}


@router.post("/assets/{asset_id}/refresh-metrics", response_model=schemas.JobRefOut)
def enqueue_refresh_metrics(asset_id: int, db: Session = Depends(get_db)):
    """按需刷新该素材的互动指标（worker 拉平台详情后写入新快照）。"""
    asset = _live_asset_or_404(db, asset_id)
    content = db.get(ContentItem, asset.content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="素材内容不存在")
    job = enqueue(db, "refresh_metrics", {"content_id": content.id})
    return {"job_id": job.id}


@router.post("/assets/refresh-metrics/bulk", response_model=schemas.BulkRefreshOut)
def enqueue_bulk_refresh_metrics(body: BulkRefreshMetricsRequest, db: Session = Depends(get_db)):
    """Queue one refresh per content whose latest play value is unknown or absent.

    The limit is deliberate: this is a backfill of a systematic data-quality
    gap, not an unbounded crawl.  Existing queued/running jobs are absorbed by
    queue deduplication, so repeated clicks are safe and report only new work.
    Only content types and platforms that can actually produce a play counter
    are eligible; otherwise articles would remain permanently "missing".
    """
    from app.domain.capabilities import supports_metric

    if body.platform and not supports_metric(body.platform, "play"):
        return {"matched": 0, "job_ids": []}
    play_platforms = [
        platform
        for platform in db.scalars(select(ContentItem.platform).distinct()).all()
        if supports_metric(platform, "play")
    ]
    if not play_platforms:
        return {"matched": 0, "job_ids": []}
    latest = (
        select(
            ContentMetricSnapshot.id.label("snapshot_id"),
            ContentMetricSnapshot.content_id,
            ContentMetricSnapshot.play,
            func.row_number()
            .over(
                partition_by=ContentMetricSnapshot.content_id,
                order_by=(ContentMetricSnapshot.captured_at.desc(), ContentMetricSnapshot.id.desc()),
            )
            .label("rn"),
        )
        .subquery()
    )
    stmt = (
        select(ContentItem.id)
        .outerjoin(latest, latest.c.content_id == ContentItem.id)
        .where(
            ContentItem.content_type == "video",
            ContentItem.platform.in_(play_platforms),
            or_(latest.c.snapshot_id.is_(None), latest.c.play.is_(None)),
        )
    )
    if body.account_id is not None:
        stmt = stmt.where(ContentItem.account_id == body.account_id)
    if body.platform:
        stmt = stmt.where(ContentItem.platform == body.platform)
    content_ids = list(db.scalars(stmt.order_by(ContentItem.id).limit(body.limit)).all())
    job_ids: list[int] = []
    for content_id in content_ids:
        job = enqueue(db, "refresh_metrics", {"content_id": content_id}, commit=False)
        if job.id not in job_ids:
            job_ids.append(job.id)
    db.commit()
    return {"matched": len(content_ids), "job_ids": job_ids}


@router.get("/assets/{asset_id}/understanding", response_model=schemas.UnderstandingOut)
def get_understanding(asset_id: int, db: Session = Depends(get_db)):
    """高级档视频理解结果；尚未生成时返回 status=pending（含素材当前状态）。"""
    asset = _live_asset_or_404(db, asset_id)
    row = db.scalar(select(VideoUnderstanding).where(VideoUnderstanding.asset_id == asset_id))
    if row is None:
        return {
            "asset_id": asset_id,
            "status": "pending",
            "process_tier": asset.process_tier,
            "understand_status": asset.understand_status,
            "error": asset.error or "",
        }
    return {
        "asset_id": asset_id,
        "status": row.status,
        "process_tier": asset.process_tier,
        "understand_status": asset.understand_status,
        "category": row.category,
        "category_confidence": row.category_confidence,
        "category_reason": row.category_reason,
        "low_confidence": row.low_confidence,
        "dimensions": row.dimensions,
        "summary": row.summary,
        "timeline": row.timeline,
        "model": row.model,
        "prompt_version": row.prompt_version,
        "usage": row.usage,
        "error": row.error or asset.error or "",
    }


def _breakdown_payload(row: ViralBreakdown) -> dict:
    """把 ViralBreakdown 行序列化为 API 响应（含报告全部字段）。"""
    return {
        "asset_id": row.asset_id,
        "status": row.status,
        "score": row.score,
        "verdict": row.verdict,
        "sections": row.sections or {},
        "comment_insights": row.comment_insights or {},
        "data_context": row.data_context or {},
        "understanding_available": row.understanding_available,
        "model": row.model,
        "prompt_version": row.prompt_version,
        "usage": row.usage or {},
        "error": row.error or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/assets/{asset_id}/breakdown", response_model=schemas.JobRefOut)
def enqueue_breakdown(
    asset_id: int, body: BreakdownRequest | None = None, db: Session = Depends(get_db)
):
    """入队爆款拆解（worker 消费）；素材不存在 404，force 非布尔 422。

    - 无报告：建 pending 行并入队。
    - 已 done 且 force=False：worker 会短路返回既有结果（不重复花 token），状态保持 done。
    - force=True：置 pending 重跑。
    """
    asset = _live_asset_or_404(db, asset_id)
    force = bool(body.force) if body else False
    row = db.scalar(select(ViralBreakdown).where(ViralBreakdown.asset_id == asset_id))
    if row is None:
        row = ViralBreakdown(asset_id=asset_id, status="pending")
        db.add(row)
    elif not (row.status == "done" and not force):
        # 非「已完成且无需重跑」的既有行 → 立即置 pending，让 GET 立刻可读。
        row.status = "pending"
        row.error = ""
    from app.services.workflow_engine import register_workflow

    result = register_workflow(
        db,
        asset_id,
        ["breakdown"],
        options={"force": force},
        force=["breakdown"] if force else (),
        commit=True,
    )
    from app.models import PipelineStep

    step = db.scalar(
        select(PipelineStep).where(
            PipelineStep.run_id == result.run.id,
            PipelineStep.step == "breakdown",
        )
    )
    return {
        "job_id": step.job_id if step is not None else None,
        "run_id": result.run.id,
        "status": step.status if step is not None else "waiting_prerequisite",
        **_ai_schedule_hold(db),
    }


@router.get(
    "/assets/{asset_id}/breakdown",
    response_model=schemas.BreakdownPending | schemas.BreakdownReport,
)
def get_breakdown(asset_id: int, db: Session = Depends(get_db)):
    """读取爆款拆解报告；素材不存在 404，尚未生成返回 ``{"status":"pending"}``。"""
    _live_asset_or_404(db, asset_id)
    from app.services.workflow_engine import public_status

    pipeline = public_status(db, asset_id)
    row = db.scalar(select(ViralBreakdown).where(ViralBreakdown.asset_id == asset_id))
    if row is None or row.status != "done":
        # 未生成 / 进行中 / 失败：给轻量状态（失败附 error），不返回半成品报告。
        return {
            "asset_id": asset_id,
            "status": row.status if row is not None else "pending",
            "error": (row.error or "") if row is not None else "",
            "pipeline": pipeline,
        }
    payload = _breakdown_payload(row)
    payload["pipeline"] = pipeline
    return payload


def _known_categories(db: Session) -> set[str]:
    from app.core.process_settings import get_taxonomy

    return set(get_taxonomy(db).keys())


@router.post("/assets/{asset_id}/process", response_model=schemas.JobRefOut)
def enqueue_process(asset_id: int, body: ProcessRequest, db: Session = Depends(get_db)):
    """手动分档加工：basic → 转录；advanced → 视频理解（可 force_category / use_cover 覆盖）。"""
    asset = _live_asset_or_404(db, asset_id)
    tier = (body.tier or "").strip().lower()
    if tier not in ("basic", "advanced"):
        raise HTTPException(status_code=422, detail="tier 仅支持 basic / advanced")

    if tier == "basic":
        from app.services.workflow_engine import WorkflowInputError, register_workflow

        try:
            result = register_workflow(db, asset_id, ["transcribe"], force=["transcribe"])
        except WorkflowInputError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        asset.transcribe_status = "pending"
        # 记录「普通」档（不得把已完成的「高级」降级回 basic）。
        if asset.process_tier != "advanced":
            asset.process_tier = "basic"
        db.commit()
        return {
            "job_id": result.queued_jobs[0] if result.queued_jobs else None,
            "run_id": result.run.id,
            "tier": tier,
        }

    payload: dict = {"asset_id": asset_id}
    if body.force_category:
        if body.force_category not in _known_categories(db):
            raise HTTPException(status_code=422, detail=f"未知视频类型: {body.force_category}")
        payload["force_category"] = body.force_category
    if body.use_cover is not None:
        payload["use_cover"] = body.use_cover
    from app.services.workflow_engine import WorkflowInputError, register_workflow

    try:
        result = register_workflow(
            db,
            asset_id,
            ["understand_video"],
            options={"force_category": body.force_category, "use_cover": body.use_cover},
            force=["understand_video"],
        )
    except WorkflowInputError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    asset.understand_status = "pending"
    db.commit()
    return {
        "job_id": result.queued_jobs[0] if result.queued_jobs else None,
        "run_id": result.run.id,
        "tier": tier,
        **_ai_schedule_hold(db),
    }


@router.post("/assets/{asset_id}/reclassify", response_model=schemas.JobRefOut)
def enqueue_reclassify(
    asset_id: int, body: ReclassifyRequest | None = None, db: Session = Depends(get_db)
):
    """按当前设置重跑判类与视频理解（忽略已有结果）。"""
    asset = _live_asset_or_404(db, asset_id)
    body = body or ReclassifyRequest()
    payload: dict = {"asset_id": asset_id}
    force = body.force_category
    if force:
        if force not in _known_categories(db):
            raise HTTPException(status_code=422, detail=f"未知视频类型: {force}")
        payload["force_category"] = force
    if body.use_cover is not None:
        payload["use_cover"] = body.use_cover
    from app.services.workflow_engine import WorkflowInputError, register_workflow

    try:
        result = register_workflow(
            db,
            asset_id,
            ["understand_video"],
            options={"force_category": force, "use_cover": body.use_cover},
            force=["understand_video"],
        )
    except WorkflowInputError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    asset.understand_status = "pending"
    db.commit()
    return {
        "job_id": result.queued_jobs[0] if result.queued_jobs else None,
        "run_id": result.run.id,
        **_ai_schedule_hold(db),
    }


@router.post("/assets/export", response_model=schemas.ExportOut | list[schemas.ExportDoc], response_model_exclude_none=True)
def export_assets(body: ExportRequest, db: Session = Depends(get_db)):
    docs = []
    for asset_id in body.asset_ids:
        asset = db.get(MediaAsset, asset_id)
        if asset is None or asset.deleted_at is not None:
            # Export is a bulk operation: skip stale/deleted ids instead of
            # failing the whole request because one selection vanished.
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


# ---------- 归档博主头像（本地缓存，来自抖小云 .actors） ----------
@router.get("/archive/avatar/{name}")
def archive_avatar(name: str):
    """服务索引阶段从 .actors 复制出的博主头像；name 已做安全化，限制在本目录内。"""
    from app.config import settings

    try:
        path = resolve_archive_avatar(name, settings.data_dir)
    except ArchiveAvatarInvalid as exc:
        raise HTTPException(status_code=404, detail="头像不存在") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="头像不存在")
    return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
