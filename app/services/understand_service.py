"""高级档组合服务：判类 → 选类型提示词 → 视频理解 → 落库。

两遍式视频理解的服务层（设计 §3、计划 Task 7）：

    content/transcript ──①判类(文本)──▶ 类型 ──②选提示词──▶ ③vision 理解 ──▶ video_understanding

- 判类失败 → 回退 `other` + 通用模板，**不阻断**理解（设计 §3「失败隔离」）；置信度低于阈值
  则标 `low_confidence` 供人工复核（设计 §4）。
- `use_cover` 开启时按 `content.cover_url` 下载封面到 `_cover/` 再送入分类器（设计 §12-4）；
  下载失败/超限回退纯文本，不阻断。
- 提示词按类型区分：`build_prompt` 拼「角色/关注点/维度/输出 JSON/禁止编造」。
- 体积超限先用 ffmpeg 逐级降质压缩（`_ensure_inline`），压缩后仍超限交给 vision 抛错由 worker 重试。
- 判类与理解走 **DB 设置优先、config 兜底**，保证设置页可在线改（含 llm_* 覆盖）。

对外能力：
- `build_prompt(...)`：纯函数，拼装类型专属理解提示词。
- `async run_understand(db, asset_id, *, force_category=None, use_cover=None)`：跑完整链路，返回
  `VideoUnderstanding`；理解失败会把 `status` 落 `error` 并**原样抛出**（worker 负责重试）。
- `UnderstandInputError`：素材/内容缺失等数据问题——不可重试，worker 应直接失败。
"""

import logging
import os
import subprocess

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import events
from app.core.process_settings import (
    get_common_dimensions,
    get_process_settings,
    get_taxonomy,
    get_type_prompts,
)
from app.core.storage import resolve_asset_dir
from app.models import ContentItem, MediaAsset, SystemSetting, Transcript, VideoUnderstanding
from app.modules import vision
from app.modules.classify import classify

logger = logging.getLogger("platform.understand")

_MB = 1024 * 1024
_PROMPT_VERSION = "v1"
# 转录送入视频理解的截断长度（理解默认已「看视频」，转录只作补充）。
_TRANSCRIPT_LIMIT = 2000
# 封面下载超时（秒）。
_COVER_TIMEOUT = 20


class UnderstandInputError(ValueError):
    """输入/数据问题导致理解不可执行（如资产无关联内容）——不应被当作可重试故障。

    继承 ValueError（调用方/测试可用 ValueError 捕获）。worker 侧据此不重试。
    """


# --------------------------------------------------------------------------- 配置解析


def _resolve_llm(db: Session) -> tuple[str, str, str]:
    """解析 LLM Base URL / Key / 模型：DB 设置优先，回退 config。

    与 `analysis.py` 不同，这里让设置页（system_setting）里的 llm_* 立即生效；
    未配置 base_url 时抛出可读错误（不静默失败）。
    """
    rows = {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}
    base = (rows.get("llm_base_url") or settings.llm_base_url or "").rstrip("/")
    key = rows.get("llm_api_key") or settings.llm_api_key or ""
    model = rows.get("llm_model") or settings.llm_model or ""
    if not base:
        raise ValueError("未配置大模型 Base URL（在系统设置里填）")
    return base, key, model


# --------------------------------------------------------------------------- 提示词


def build_prompt(
    category: str,
    label: str,
    focus: str,
    dimensions: list[dict],
    transcript: str,
) -> str:
    """按类型拼装理解提示词（纯函数）。

    Args:
        category: 类型枚举（写进输出 JSON 的 ``type``）。
        label: 类型中文名（角色描述用）。
        focus: 该类型的「关注点」段落（类型区分的灵魂）。
        dimensions: 该类型维度清单（通用 + 专属），每项 ``{key,name,ask}``。
        transcript: 转录全文（可为空）。
    """
    dim_lines = "\n".join(f"- {d['key']}（{d['name']}）：{d['ask']}" for d in dimensions)
    body = (
        f"【角色】你是短视频内容分析师，专长「{label}」类视频。\n"
        f"【任务】观看视频，输出该类型的内容理解结果。\n"
        f"【本类型关注点】{focus}\n"
        f"【维度定义】逐条判断下列维度：\n{dim_lines}\n"
        "【输出格式】只输出 JSON，不要解释、不要 Markdown 代码块：\n"
        f'{{"type": "{category}", "summary": "<100字内整体概括>", '
        '"dimensions": {"<维度key>": "<值>"}, '
        '"timeline": [{"t": <秒>, "event": "<发生了什么>"}], "confidence": 0.0}\n'
        "【约束】只依据视频与给定转录；不确定或看不到的字段填 null；"
        "不要编造品牌、人名、价格等视频中不存在的细节。"
    )
    text = (transcript or "").strip()[:_TRANSCRIPT_LIMIT]
    if text:
        body += f"\n【参考转录】\n{text}"
    return body


def _select_prompt(db: Session, category: str) -> tuple[str, str, str, list[dict]]:
    """返回该类型的 (category, label, focus, dimensions)；未知类型归一为 other。"""
    taxonomy = get_taxonomy(db)
    prompts = get_type_prompts(db)
    if category not in taxonomy:
        category = "other"
    if category not in prompts:
        category = "other"
    label = taxonomy.get(category, "其他")
    focus = prompts[category].get("focus", "")
    dimensions = get_common_dimensions(db) + list(prompts[category].get("dimensions", []))
    return category, label, focus, dimensions


async def _classify_category(
    db: Session,
    content: ContentItem,
    transcript: str,
    *,
    use_cover: bool,
    cover_path: str | None,
    base_url: str,
    api_key: str,
    model: str,
) -> tuple[str, float, str]:
    """判类；失败回退 other 且不抛（失败隔离）。返回 (category, confidence, reason)。"""
    try:
        result = await classify(
            content.title,
            content.author_name,
            content.duration_sec,
            transcript,
            get_taxonomy(db),
            cover_path=cover_path,
            base_url=base_url,
            api_key=api_key,
            model=model,
            use_cover=use_cover,
        )
        cat = result.get("type") or "other"
        conf = _coerce_conf(result.get("confidence"))
        reason = result.get("reason") or ""
        return cat, conf, reason
    except Exception as exc:  # noqa: BLE001 - 判类任何失败都不应阻断理解
        logger.warning("判类失败，回退 other: %s", exc)
        return "other", 0.0, f"判类失败，回退通用：{exc}"


def _coerce_conf(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if num != num:
        return 0.0
    return max(0.0, min(1.0, num))


# --------------------------------------------------------------------------- 路径 / 压缩


def _asset_video_path(db: Session, asset: MediaAsset) -> str:
    """素材物理视频路径 = storage_root + relative_path + video_file。仅后端内部使用。"""
    return os.path.join(resolve_asset_dir(db, asset), asset.video_file or "")


def _guess_ext(url: str, content_type: str) -> str:
    """按响应 Content-Type 猜封面扩展名，兜底 .jpg。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    return {
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(ct, ".jpg")


async def _ensure_cover(content: ContentItem, work_dir: str, max_mb: float) -> str | None:
    """下载 `content.cover_url` 缓存到素材目录，返回本地路径；无封面/不可用返回 None。

    为什么在服务层做：设计 §12-4 用户拍板「封面可选，开启才额外送图片模型」，
    但封面此前从未落盘，判类永远拿不到封面（Task 7 review Important#1）。

    失败处理：下载失败 / 体积超 `max_mb` / 空内容 → 记 warning 并返回 None（回退纯文本判类，
    不阻断理解）；已在本地缓存则直接复用，避免重复下载。
    """
    url = (content.cover_url or "").strip()
    if not url:
        return None
    os.makedirs(work_dir, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=_COVER_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.content
        if not raw:
            logger.warning("封面下载为空，跳过封面判类: %s", url)
            return None
        if max_mb and max_mb > 0 and len(raw) > max_mb * _MB:
            logger.warning("封面体积 %.2fMB 超过上限 %.2fMB，跳过封面判类", len(raw) / _MB, max_mb)
            return None
        path = os.path.join(work_dir, "cover" + _guess_ext(url, resp.headers.get("content-type", "")))
        with open(path, "wb") as fh:
            fh.write(raw)
        logger.info("封面已缓存: %s -> %s", url, path)
        return path
    except Exception as exc:  # noqa: BLE001 - 封面是可选增强，失败一律回退纯文本
        logger.warning("封面下载失败，回退纯文本判类: %s", exc)
        return None


def _ensure_inline(video_path: str, max_mb: float, work_dir: str) -> str:
    """体积超限时用 ffmpeg 降质压缩到内联上限以下，返回可送入模型的路径。

    未超限 / 文件不可读 / max_mb<=0 → 原样返回。压缩失败抛 RuntimeError（带 stderr 摘要）。
    """
    if max_mb and max_mb > 0:
        try:
            size = os.path.getsize(video_path)
        except OSError:
            return video_path
        if size > max_mb * _MB:
            os.makedirs(work_dir, exist_ok=True)
            out = os.path.join(work_dir, "inline.mp4")
            # 逐级降质：720p/CRF30 → 480p/CRF34。低码率长视频压一次可能仍超限，
            # 压完仍超限则再降一档；两档后返回，由 vision.understand 体积校验兜底。
            attempts = [("720", "30"), ("480", "34")]
            for scale_h, crf in attempts:
                # `scale=-2:min(<h>\,ih)`：宽度 -2 保持偶数比例，高度不放大超过原高。
                proc = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", video_path,
                        "-vf", rf"scale=-2:min({scale_h}\,ih)",
                        "-c:v", "libx264", "-crf", crf, "-preset", "veryfast",
                        "-c:a", "aac", "-b:a", "96k", out,
                    ],
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0 or not os.path.exists(out):
                    tail = (proc.stderr or "").strip().splitlines()[-3:]
                    raise RuntimeError("ffmpeg 压缩视频失败: " + " | ".join(tail))
                if os.path.getsize(out) <= max_mb * _MB:
                    break
            logger.info("视频内联压缩: %s -> %s", video_path, out)
            return out
    return video_path


# --------------------------------------------------------------------------- 组合服务


def _transcript_text(db: Session, asset_id: int) -> str:
    tr = db.scalar(select(Transcript).where(Transcript.asset_id == asset_id))
    return tr.full_text if tr and tr.full_text else ""


async def run_understand(
    db: Session,
    asset_id: int,
    *,
    force_category: str | None = None,
    use_cover: bool | None = None,
) -> VideoUnderstanding:
    """跑完整高级档链路，返回并落库 `VideoUnderstanding`。

    Raises:
        UnderstandInputError: 素材不存在 / 无关联内容（数据问题，不可重试）。
        Exception: 理解链路任何失败——落 `status=error` 后原样抛出（worker 负责重试）。
    """
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise UnderstandInputError(f"素材不存在: {asset_id}")

    asset.understand_status = "doing"
    db.commit()
    events.publish("asset_status", {"asset_id": asset_id, "understand": "doing"})

    try:
        asset = db.get(MediaAsset, asset_id)
        content = db.get(ContentItem, asset.content_id)
        if content is None:  # 数据不一致（孤儿资产）：显式报错，不靠兜底 catch 静默成 other
            raise UnderstandInputError(f"素材 #{asset_id} 未关联内容（content_id={asset.content_id}）")
        s = get_process_settings(db)
        base_url, api_key, llm_model = _resolve_llm(db)

        transcript = _transcript_text(db, asset_id)

        # ① 判类（可强制类型 / 可关闭）；开启封面时把封面下载到本地再送入分类器
        want_cover = s["use_cover"] if use_cover is None else use_cover
        if force_category:
            # 人工指定类型：置信度记 1.0，理由注明来源，跳过判类。
            category, conf, reason = force_category, 1.0, "人工指定类型"
        elif s.get("classify_enabled", True):
            cover_path = None
            if want_cover:
                work_dir = os.path.join(resolve_asset_dir(db, asset), "_cover")
                cover_path = await _ensure_cover(content, work_dir, float(s.get("cover_max_mb", 5)))
            category, conf, reason = await _classify_category(
                db,
                content,
                transcript,
                use_cover=want_cover,
                cover_path=cover_path,
                base_url=base_url,
                api_key=api_key,
                model=s.get("classify_model") or llm_model,
            )
        else:
            category, conf, reason = "other", 0.0, "判类已关闭，使用通用模板"

        # 低置信度标记（设计 §4：低于阈值 → low_confidence 供人工复核）
        threshold = float(s.get("low_confidence_threshold", 0.6))
        low_confidence = bool(conf < threshold)

        # ② 选提示词（未知类型归一 other）
        category, label, focus, dimensions = _select_prompt(db, category)
        prompt = build_prompt(category, label, focus, dimensions, transcript)

        # ③ 视频理解（超限先压缩）
        video_path = _asset_video_path(db, asset)
        work_dir = os.path.join(os.path.dirname(video_path), "_inline")
        inline_path = _ensure_inline(video_path, float(s.get("video_max_mb", 15)), work_dir)
        result = await vision.understand(
            inline_path,
            prompt,
            provider=s.get("provider", "qwen"),
            base_url=base_url,
            api_key=api_key,
            model=s.get("vision_model") or llm_model,
        )

        parsed = vision.parse_structured(result.get("text", ""))
        summary = parsed.get("summary") if isinstance(parsed.get("summary"), str) else ""
        dims = parsed.get("dimensions") if isinstance(parsed.get("dimensions"), dict) else {}
        dims = {str(k): v for k, v in dims.items()}
        timeline = parsed.get("timeline") if isinstance(parsed.get("timeline"), list) else []
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}

        existing = db.scalar(select(VideoUnderstanding).where(VideoUnderstanding.asset_id == asset_id))
        if existing is None:
            existing = VideoUnderstanding(asset_id=asset_id)
            db.add(existing)
        existing.category = category
        existing.category_confidence = conf
        existing.category_reason = reason[:512]
        existing.low_confidence = low_confidence
        existing.dimensions = dims
        existing.summary = summary
        existing.timeline = timeline
        existing.model = result.get("model", "") or llm_model
        existing.prompt_version = f"{_PROMPT_VERSION}:{category}"
        existing.usage = usage
        existing.status = "done"
        existing.error = ""

        content.auto_category = category
        content.category_confidence = conf
        asset.understand_status = "done"
        asset.process_tier = "advanced"
        # 注意：不要写 asset.error。media_asset.error 是**共享**的下载/整体错误位，
        # 视频理解失败时写进去会让「已下载成功」的素材在前端显示成失败（实测 bug）。
        # 理解自身的错误只落 VideoUnderstanding.error。
        db.commit()
        db.refresh(existing)
        events.publish("asset_status", {"asset_id": asset_id, "understand": "done"})
        return existing
    except Exception as exc:
        db.rollback()
        _record_understand_error(db, asset_id, str(exc))
        events.publish("asset_status", {"asset_id": asset_id, "understand": "error"})
        raise


def _record_understand_error(db: Session, asset_id: int, message: str) -> None:
    """把理解失败落到理解自己的记录上，绝不污染 media_asset.error。"""
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        return
    asset.understand_status = "error"
    row = db.scalar(select(VideoUnderstanding).where(VideoUnderstanding.asset_id == asset_id))
    if row is None:
        row = VideoUnderstanding(asset_id=asset_id)
        db.add(row)
    row.status = "error"
    row.error = message[:512]
    db.commit()
