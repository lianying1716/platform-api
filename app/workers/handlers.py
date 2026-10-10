"""任务处理器：按 job_type 分发。

- download：yt-dlp 下载视频到 storage_root + relative_path，抽音频备转录。
- transcribe：faster-whisper 转录音频/视频，写 transcript。
- analyze：调外部大模型对转录全文做摘要/标签，写 analysis_result。
- sync：执行一次订阅同步（也可由调度器直接调）。
- crawl：执行一次关键词采集。

每个 handler 形如 async def(db, payload) -> None，抛异常即视为失败（runner 负责重试）。
真实下载/转录需要有效凭证与本地依赖（ffmpeg/whisper 模型），缺失时抛出可读错误。
"""

import asyncio
import logging
import random
from datetime import datetime

from sqlalchemy.orm import Session

from app.core import events, queue
from app.core.storage import render_subdir, resolve_account_root, resolve_asset_dir
from app.models import (
    AnalysisResult,
    ContentItem,
    MediaAsset,
    PlatformAccount,
    StorageRoot,
    Subscription,
    Transcript,
)
from app.modules import media_download

logger = logging.getLogger("platform.worker.handlers")

# 未指定子目录模板时的兜底（内容 ID 段保证同目录不重名）。
_DEFAULT_SUBDIR = "{platform}/{account}/{creator}/{date}/{content_id}"

# 下载串行化 + 间隔：批量同步一次会入队几十条下载，密集打 CDN 很容易触发限流。
# 用一个进程级锁把下载排成队列，并在相邻两次之间留出随机间隔（照抄抖小云下载循环里的
# Task.Delay，且抖小云整条下载 job 是 [DisallowConcurrentExecution] 串行跑的）。
_download_gate = asyncio.Lock()
_last_download_at: float = 0.0


async def _await_download_slot() -> None:
    """获取下载槽位：与上一次下载至少相隔 gap + 抖动秒。"""
    from app.config import settings

    global _last_download_at
    async with _download_gate:
        gap = max(0.0, float(getattr(settings, "download_min_gap_seconds", 2.0)))
        jitter = max(0.0, float(getattr(settings, "download_jitter_seconds", 3.0)))
        wait = 0.0
        if _last_download_at:
            elapsed = asyncio.get_running_loop().time() - _last_download_at
            wait = (gap + random.uniform(0, jitter)) - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        _last_download_at = asyncio.get_running_loop().time()


def _account_segment(account) -> str:
    """账号目录段：优先用可读的显示名，退回平台账号 ID。"""
    if account is None:
        return ""
    name = (getattr(account, "display_name", "") or "").strip()
    if name:
        return name
    return f"account-{account.id}"


def _subscription_for_content(db: Session, content: ContentItem) -> Subscription | None:
    if content.source_kind == "subscription" and content.source_id:
        return db.get(Subscription, content.source_id)
    return None


def _account_subdir_template(db: Session, content: ContentItem) -> str:
    """取内容所属订阅的子目录模板；采集/无订阅内容用默认模板。"""
    sub = _subscription_for_content(db, content)
    if sub is not None and (sub.subdir_template or "").strip():
        return sub.subdir_template
    return _DEFAULT_SUBDIR


# Linux 单个文件名上限 255 字节；留足余量（外加 .mp4、.video.m4s 等后缀）。
_MAX_NAME_BYTES = 150


def _truncate_bytes(text: str, limit: int) -> str:
    """按 UTF-8 字节截断且不截断多字节字符中间（照抄抖小云 UTF-8 字节截断做法）。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", errors="ignore")


def _download_output_name(content: ContentItem, fallback_url: str) -> str:
    """下载文件名：``标题_内容ID``（照抄抖小云 SanitizeLinuxFileName(desc, awemeId)）。

    带内容 ID 才能保证：同名不同视频不互相覆盖；标题变了也还能对上同一条素材。
    标题按**字节**截断给 ID 让位 —— 否则长标题会把 ID 截掉，既丢了唯一性又超出
    Linux 255 字节限制（中文标题按字符截 100 个可达 300 字节）。
    """
    from app.modules.media_download import _safe_name

    title = _safe_name(content.title or "")
    cid = _safe_name(content.external_id or "")
    if not cid:
        return _truncate_bytes(title, _MAX_NAME_BYTES) or _safe_name(fallback_url)
    if not title or cid in title:
        return _truncate_bytes(title or cid, _MAX_NAME_BYTES)
    # 先给 "_{cid}" 留位置，再截标题；后缀统一由 _safe_name 保证不含非法字符。
    budget = _MAX_NAME_BYTES - len(f"_{cid}".encode("utf-8"))
    if budget <= 0:
        return _truncate_bytes(cid, _MAX_NAME_BYTES)
    return f"{_truncate_bytes(title, budget)}_{cid}"


async def _download_content(content: ContentItem, target_dir, cookie: str):
    """按平台选择下载方式。

    - 抖音：抓取时已存 play_addr 直链 → 带 Cookie 直连下载（照抄抖小云，
      不依赖 yt-dlp；页面下载要 fresh cookies，很脆）。
    - B站：view + playurl 取 DASH 视频/音频流 → 多 CDN 轮换 + Range 分片并行
      + ffmpeg 合并（照抄 bili-sync）。**不用 yt-dlp**：B站对 yt-dlp 直接 412 风控。
    - 其它平台 / 老数据：回退 yt-dlp。
    """
    if content.platform == "douyin":
        extra = content.extra or {}
        direct = str(extra.get("download_url") or "")
        if direct:
            # 抖音直链带签名会过期/被限流：失败时轮换同一视频的其它镜像（照抄抖小云）。
            mirrors = [str(u) for u in (extra.get("download_urls") or []) if isinstance(u, str)]
            return await media_download.download_direct(
                direct, target_dir, cookie=cookie, mirror_urls=mirrors,
                name=_download_output_name(content, content.url),
            )
        # 老数据没有直链：按 BV/aweme 号现取一次
        return await media_download.download(content.url, target_dir, cookie=cookie)

    if content.platform == "bilibili":
        # 用 Cookie 解析 DASH；B站 CDN 只需 UA+Referer，Cookie 用于身份/高清晰度。
        bvid = str(content.external_id or "")
        if not bvid and "/video/" in (content.url or ""):
            bvid = content.url.rsplit("/video/", 1)[-1].split("/")[0]
        try:
            resolved = await _resolve_bili_urls(cookie=cookie, bvid=bvid)
        except Exception as exc:  # noqa: BLE001 解析失败回退 yt-dlp，别让整条链路断
            logger.warning("B站 playurl 解析失败（%s），回退 yt-dlp：%s", bvid, exc)
            resolved = None
        if resolved:
            return await _download_bili_pages(content, cookie, resolved, target_dir)
        return await media_download.download(content.url, target_dir, cookie=cookie)


async def _download_bili_pages(content: ContentItem, cookie: str, resolved: dict, target_dir) -> object:
    """按分P下载：单P行为不变；多P每集单独命名 + 各自字幕/弹幕/NFO。

    返回 DownloadResult（video_file 指向第一个分P产物，兼容既有状态判断）。
    """
    pages = resolved.get("pages") or [{
        "cid": resolved.get("cid"), "page": 1, "part": "",
        "video": resolved["video"], "audio": resolved.get("audio") or [],
    }]
    base_name = _download_output_name(content, content.url)
    first_result = None
    for idx, page in enumerate(pages, start=1):
        if len(pages) > 1:
            page_title = page.get("part") or f"P{page.get('page') or idx}"
            name = f"{base_name}_{idx:02d}_{page_title}"
        else:
            name = base_name
        result = await media_download.download_dash(
            page["video"], page.get("audio") or [], target_dir, name, cookie=cookie
        )
        if result.ok:
            await _write_bili_extras(content, cookie, page, target_dir, name=name)
        if first_result is None:
            first_result = result
    return first_result

    return await media_download.download(content.url, target_dir, cookie=cookie)


async def _resolve_bili_urls(*, cookie: str, bvid: str):
    """把 cookie 串还原成适配器需要的 credential dict，再解析 DASH 地址。"""
    from app.modules import bilibili as bili_module

    cred: dict = {}
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k in ("SESSDATA", "bili_jct", "buvid3", "DedeUserID", "ac_time_value"):
            cred[k.lower() if k != "DedeUserID" else "dedeuserid"] = v.strip()
    return await bili_module.resolve_play_urls(cred, bvid)


async def _write_bili_extras(content: ContentItem, cookie: str, resolved: dict, target_dir,
                             name: str | None = None) -> None:
    """B站下载成功后补齐字幕/弹幕/NFO（对齐 bili-sync 的媒体库体验）。

    属于增强项：任一失败只记日志，绝不翻转已完成的下载状态。
    """
    from app.modules import bili_extras

    bvid = str(content.external_id or "")
    cid = resolved.get("cid")
    if not bvid or not cid:
        return
    cred: dict = {}
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k in ("SESSDATA", "bili_jct", "buvid3", "DedeUserID", "ac_time_value"):
            cred[k.lower() if k != "DedeUserID" else "dedeuserid"] = v.strip()
    try:
        await bili_extras.fetch_subtitles(cred, bvid, int(cid), target_dir, stem=name)
    except Exception:  # noqa: BLE001 增强项失败不翻转下载
        logger.warning("B站字幕抓取失败 %s，已忽略", bvid, exc_info=True)
    try:
        await bili_extras.fetch_danmaku(
            cred, bvid, int(cid), resolved.get("duration") or content.duration_sec or 0,
            target_dir, stem=name,
        )
    except Exception:  # noqa: BLE001
        logger.warning("B站弹幕抓取失败 %s，已忽略", bvid, exc_info=True)
    try:
        published = content.published_at.isoformat() if content.published_at else None
        bili_extras.write_nfo(
            target_dir,
            name or bvid,
            title=content.title or bvid,
            author=content.author_name or "",
            published_at=published,
            bvid=bvid,
        )
    except Exception:  # noqa: BLE001
        logger.warning("B站 NFO 写入失败 %s，已忽略", bvid, exc_info=True)


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

    # 计算保存目录：根目录优先取订阅级覆盖，其次账号绑定，最后默认根 + 订阅子目录模板。
    account = db.get(PlatformAccount, content.account_id)
    sub = _subscription_for_content(db, content)
    root = None
    if sub is not None and sub.storage_root_id is not None:
        root = db.get(StorageRoot, sub.storage_root_id)
    if root is None:
        root = resolve_account_root(db, account)
    if asset.storage_root_id is None and root is not None:
        asset.storage_root_id = root.id
    template = _account_subdir_template(db, content)
    rel = asset.relative_path or render_subdir(
        template,
        platform=content.platform,
        account=_account_segment(account),
        creator=content.author_name,
        date=content.published_at or content.created_at,
        content_id=content.external_id,
    )
    asset.relative_path = rel
    target_dir = resolve_asset_dir(db, asset)

    # 下载策略按平台分派（见 _download_content）：
    # 抖音走 play_addr 直链 + Cookie（照抄抖小云），B站走 playurl DASH + ffmpeg（照抄 bili-sync）。
    cookie = _content_cookie(db, content)
    # 节流：所有下载共用一条串行通道 + 最小间隔，避免批量同步打爆 CDN 触发限流。
    await _await_download_slot()
    result = await _download_content(content, target_dir, cookie)
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
    from app.services.auto_process import enqueue_auto_process
    from app.services.process_gate import enqueue_process_jobs

    settings = get_process_settings(db)
    try:
        enqueue_process_jobs(db, [content], settings=settings)
    except Exception:  # noqa: BLE001 分档加工是增强项，失败不得翻转已完成的下载
        db.rollback()
        logger.exception("素材 #%s 下载后分档入队失败，已忽略", asset_id)
    # 显式自动转录 / 自动分析（订阅级或全局默认）：绕过热度门控直接入队。
    try:
        enqueue_auto_process(db, asset, content, settings)
        db.commit()
    except Exception:  # noqa: BLE001 自动加工是增强项，失败不得翻转已完成的下载
        db.rollback()
        logger.exception("素材 #%s 自动加工入队失败，已忽略", asset_id)


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
    # 转录完成后再补自动分析（analyze 依赖转录文本，必须在此链式触发）。
    try:
        from app.core.process_settings import get_process_settings
        from app.services.auto_process import maybe_chain_analyze

        content = db.get(ContentItem, asset.content_id)
        maybe_chain_analyze(db, asset, content, get_process_settings(db))
        db.commit()
    except Exception:  # noqa: BLE001 自动分析是增强项，失败不得翻转已完成的转录
        db.rollback()
        logger.exception("素材 #%s 转录后自动分析入队失败，已忽略", asset_id)


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


def _content_cookie(db: Session, content: ContentItem) -> str:
    """取内容所属账号的 cookie；系统账号/无凭证返回空。"""
    from app.core.crypto import decrypt_credential
    from app.models import PlatformAccount
    from app.services.crawl_service import _account_cookie

    account = db.get(PlatformAccount, content.account_id)
    if account is None or account.is_system or account.status != "active":
        return ""
    return _account_cookie(account)


def _ts_to_dt(ts) -> datetime | None:
    try:
        return datetime.utcfromtimestamp(int(ts)) if ts else None
    except (TypeError, ValueError):
        return None


async def handle_fetch_comments(db: Session, payload: dict) -> None:
    """抓作品一级评论（抖音/B站）：复用账号 cookie，按配置抓 K 页取 Top-N，幂等写库 + state。"""
    from app.core.comment_settings import get_comment_settings
    from app.models import Comment, CommentFetchState, ContentItem
    from app.modules import crawler

    content_id = payload["content_id"]
    content = db.get(ContentItem, content_id)
    if content is None:
        return
    # 仅白名单平台有评论适配器。非白名单 content 直接跳过，避免无适配器时抛错重试。
    from app.services.comment_gate import COMMENT_PLATFORMS

    if content.platform not in COMMENT_PLATFORMS:
        logger.info("跳过非评论平台的内容 %s（platform=%s）", content_id, content.platform)
        return

    cookie = _content_cookie(db, content)
    if not cookie:
        raise ValueError(f"{content.platform} 无在线账号，无法抓评论")

    s = get_comment_settings(db)
    comments = await crawler.fetch_comments(
        aweme_id=content.external_id, cookie=cookie,
        pages=s["pages"], per_page=s["per_page"], top_n=s["top_n"], interval=s["interval"],
        platform=content.platform,
        extra={**(getattr(content, "extra", None) or {}), "url": content.url},
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


async def handle_refresh_metrics(db: Session, payload: dict) -> None:
    """刷新单条内容的互动指标快照（评论门控/趋势分析依赖最新值）。"""
    from app.models import ContentMetricSnapshot, ContentItem
    from app.modules import content_metrics

    content = db.get(ContentItem, payload["content_id"])
    if content is None:
        return
    cookie = _content_cookie(db, content)
    if not cookie:
        raise ValueError(f"{content.platform} 无在线账号，无法刷新指标")
    metrics = await content_metrics.fetch_metrics(
        content.platform, content.external_id, cookie, getattr(content, "extra", None)
    )
    if not metrics:
        raise ValueError(f"{content.platform} 暂不支持指标刷新或返回为空")
    db.add(ContentMetricSnapshot(
        content_id=content.id,
        play=metrics.get("play", 0),
        like=metrics.get("like", 0),
        comment=metrics.get("comment", 0),
        favorite=metrics.get("favorite", 0),
        share=metrics.get("share", 0),
    ))
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
    "refresh_metrics": handle_refresh_metrics,
    "understand_video": handle_understand_video,
    "breakdown": handle_breakdown,
}
