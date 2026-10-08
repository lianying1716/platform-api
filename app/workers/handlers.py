"""任务处理器：按 job_type 分发。

- download：yt-dlp 下载视频到 storage_root + relative_path，抽音频备转录。
- transcribe：faster-whisper 转录音频/视频，写 transcript。
- analyze：调外部大模型对转录全文做摘要/标签，写 analysis_result。
- sync：执行一次订阅同步（也可由调度器直接调）。
- crawl：执行一次关键词采集。

每个 handler 形如 async def(db, payload) -> None，抛异常即视为失败（runner 负责重试）。
真实下载/转录需要有效凭证与本地依赖（ffmpeg/whisper 模型），缺失时抛出可读错误。
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.core import events, queue
from app.core.storage import get_default_root, render_subdir, resolve_asset_dir
from app.models import AnalysisResult, ContentItem, MediaAsset, Transcript
from app.modules import media_download

logger = logging.getLogger("platform.worker.handlers")


async def handle_download(db: Session, payload: dict) -> None:
    asset_id = payload["asset_id"]
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        return
    content = db.get(ContentItem, asset.content_id)
    if content is None or not content.url:
        raise ValueError("内容缺少可下载 URL")

    asset.download_status = "downloading"
    db.commit()
    events.publish("asset_status", {"asset_id": asset_id, "download": "downloading"})

    # 计算保存目录
    root = get_default_root(db)
    if asset.storage_root_id is None and root is not None:
        asset.storage_root_id = root.id
    rel = asset.relative_path or render_subdir(
        "{platform}/{creator}/{date}/{content_id}",
        platform=content.platform,
        creator=content.author_name,
        date=content.published_at or content.created_at,
        content_id=content.external_id,
    )
    asset.relative_path = rel
    target_dir = resolve_asset_dir(db, asset)

    # 抖音的页面下载需要带 Cookie（yt-dlp 的 Douyin extractor 明确要求 fresh cookies），
    # 没带就会直接失败。其它平台带了也无害。
    cookie = _comment_cookie(db, content.platform)
    result = await media_download.download(content.url, target_dir, cookie=cookie)
    # download() 返回 DownloadResult(dataclass)，不是 dict —— 这里必须属性访问。
    asset.video_file = result.video_file
    asset.audio_file = result.audio_file
    if not result.ok:
        # 下载失败别谎报 done，否则看板上全是"已完成"却没有文件
        asset.download_status = "error"
        asset.error = result.message or "下载失败"
        db.commit()
        events.publish("asset_status", {"asset_id": asset_id, "download": "error"})
        return
    asset.download_status = "done"
    asset.error = ""
    db.commit()
    events.publish("asset_status", {"asset_id": asset_id, "download": "done"})
    # 下载完成后按动态热度门控自动决定是否升级到高级档（设计 §6.1）。采集/同步时素材尚未
    # 下载完，门控只对已下载素材生效，故真正的自动分档在此触发。失败不得翻转下载成功状态。
    from app.core.process_settings import get_process_settings
    from app.services.process_gate import enqueue_process_jobs

    try:
        enqueue_process_jobs(db, [content], settings=get_process_settings(db))
    except Exception:  # noqa: BLE001 分档加工是增强项，失败不得翻转已完成的下载
        db.rollback()
        logger.exception("素材 #%s 下载后分档入队失败，已忽略", asset_id)


async def handle_transcribe(db: Session, payload: dict) -> None:
    from app.modules import transcription

    asset_id = payload["asset_id"]
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        return
    if asset.download_status != "done":
        raise ValueError("素材尚未下载完成，无法转录")

    asset.transcribe_status = "transcribing"
    db.commit()
    events.publish("asset_status", {"asset_id": asset_id, "transcribe": "transcribing"})

    media_path = resolve_asset_dir(db, asset)
    audio = asset.audio_file or asset.video_file
    if not audio:
        raise ValueError("素材没有可转录的音视频文件")
    full_media = f"{media_path}/{audio}"

    result = await transcription.transcribe(full_media, payload.get("options", {}))

    existing = db.query(Transcript).filter(Transcript.asset_id == asset_id).first()
    if existing is None:
        existing = Transcript(asset_id=asset_id)
        db.add(existing)
    existing.language = result["language"]
    existing.model = result["model"]
    existing.segments = result["segments"]
    existing.full_text = result["full_text"]
    asset.transcribe_status = "done"
    # 记录「普通」档：转录即 basic。不得把高级（视频理解）降级回 basic。
    if asset.process_tier != "advanced":
        asset.process_tier = "basic"
    asset.error = ""
    db.commit()
    events.publish("asset_status", {"asset_id": asset_id, "transcribe": "done"})


async def handle_analyze(db: Session, payload: dict) -> None:
    from app.modules import analysis

    asset_id = payload["asset_id"]
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        return
    transcript = db.query(Transcript).filter(Transcript.asset_id == asset_id).first()
    if transcript is None or not transcript.full_text:
        raise ValueError("素材尚无转录文本，无法分析")

    asset.analyze_status = "analyzing"
    db.commit()

    content = db.get(ContentItem, asset.content_id)
    result = await analysis.analyze(
        title=content.title if content else "",
        full_text=transcript.full_text,
    )

    existing = db.query(AnalysisResult).filter(AnalysisResult.asset_id == asset_id).first()
    if existing is None:
        existing = AnalysisResult(asset_id=asset_id)
        db.add(existing)
    existing.summary = result["summary"]
    existing.tags = result["tags"]
    existing.structured = result.get("structured", {})
    existing.model = result["model"]
    asset.analyze_status = "done"
    asset.error = ""
    db.commit()
    events.publish("asset_status", {"asset_id": asset_id, "analyze": "done"})


async def handle_sync(db: Session, payload: dict) -> None:
    from app.models import Subscription
    from app.services.sync_service import run_subscription_sync

    sub = db.get(Subscription, payload["subscription_id"])
    if sub is None:
        return
    await run_subscription_sync(db, sub)


async def handle_crawl(db: Session, payload: dict) -> None:
    from app.services.crawl_service import run_crawl_task

    await run_crawl_task(db, payload["task_id"])


def _comment_cookie(db: Session, platform: str) -> str:
    """取该平台一个在线账号的 cookie（复用 crawl_service 的逻辑）。"""
    from app.services.crawl_service import _platform_cookie

    return _platform_cookie(db, platform)


def _ts_to_dt(ts) -> datetime | None:
    try:
        return datetime.utcfromtimestamp(int(ts)) if ts else None
    except (TypeError, ValueError):
        return None


async def handle_fetch_comments(db: Session, payload: dict) -> None:
    """抓抖音作品一级评论：复用账号 cookie，按配置抓 K 页取 Top-N，幂等写库 + state。"""
    from app.core.comment_settings import get_comment_settings
    from app.models import Comment, CommentFetchState, ContentItem
    from app.modules import crawler

    content_id = payload["content_id"]
    content = db.get(ContentItem, content_id)
    if content is None:
        return
    # 仅抖音有评论适配器（本版范围）。非抖音 content 直接跳过，避免无适配器时抛错重试。
    from app.services.comment_gate import COMMENT_PLATFORMS

    if content.platform not in COMMENT_PLATFORMS:
        logger.info("跳过非评论平台的内容 %s（platform=%s）", content_id, content.platform)
        return

    cookie = _comment_cookie(db, content.platform)
    if not cookie:
        raise ValueError(f"{content.platform} 无在线账号，无法抓评论")

    s = get_comment_settings(db)
    comments = await crawler.fetch_comments(
        aweme_id=content.external_id, cookie=cookie,
        pages=s["pages"], per_page=s["per_page"], top_n=s["top_n"], interval=s["interval"],
    )

    existing = {c.comment_id for c in db.query(Comment).filter(Comment.content_id == content_id).all()}
    added = 0
    for it in comments:
        if it["comment_id"] in existing:
            continue
        db.add(Comment(
            content_id=content_id, platform=content.platform, comment_id=it["comment_id"],
            parent_comment_id=it.get("parent_comment_id", "0"), user_id=it.get("user_id", ""),
            nickname=it.get("nickname", ""), avatar=it.get("avatar", ""), content=it.get("content", ""),
            like_count=it.get("like_count", 0), ip_location=it.get("ip_location", ""),
            created_at=_ts_to_dt(it.get("created_at")),
        ))
        existing.add(it["comment_id"])
        added += 1

    # 空结果不落 state：fetch_comments 在「被风控/封禁」与「确实无评论」两种情况下都返回 []，
    # 二者无法区分。若此时新落 CommentFetchState，门控 D4「已有 state 即跳过」会让该作品
    # 永久不再重抓。仅在确有新评论入库时写 state，空结果留待下次重试。
    if added == 0:
        return

    st = db.get(CommentFetchState, content_id)
    if st is None:
        st = CommentFetchState(content_id=content_id)
        db.add(st)
    st.last_fetched_at = datetime.utcnow()
    st.fetched_count = added
    db.commit()


async def handle_understand_video(db: Session, payload: dict) -> None:
    """高级档视频理解：判类 → 类型专属提示词 → 调 vision → 写 VideoUnderstanding。"""
    from app.services.understand_service import UnderstandInputError, run_understand

    asset_id = payload["asset_id"]
    if db.get(MediaAsset, asset_id) is None:
        return
    try:
        await run_understand(
            db,
            asset_id,
            force_category=payload.get("force_category") or None,
            use_cover=payload.get("use_cover"),
        )
    except UnderstandInputError as exc:
        # 数据问题（素材/内容缺失）不可重试，直接置 error，避免无谓重试。
        raise queue.NonRetryableJobError(str(exc)) from exc


async def handle_breakdown(db: Session, payload: dict) -> None:
    """爆款拆解：拼装转录/L3/评论/确定性数字 → 调 LLM → 写 ViralBreakdown。"""
    from app.services.breakdown_service import BreakdownInputError, run_breakdown

    asset_id = payload["asset_id"]
    if db.get(MediaAsset, asset_id) is None:
        return
    try:
        await run_breakdown(db, asset_id, force=bool(payload.get("force", False)))
    except BreakdownInputError as exc:
        # 数据问题（素材/内容缺失）不可重试，直接置 error，避免无谓重试。
        raise queue.NonRetryableJobError(str(exc)) from exc


HANDLERS = {
    "download": handle_download,
    "transcribe": handle_transcribe,
    "analyze": handle_analyze,
    "sync": handle_sync,
    "crawl": handle_crawl,
    "fetch_comments": handle_fetch_comments,
    "understand_video": handle_understand_video,
    "breakdown": handle_breakdown,
}
