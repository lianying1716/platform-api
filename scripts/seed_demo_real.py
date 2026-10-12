"""把真实抓取的内容注入演示库（可选步骤，供 export_demo_snapshot 调用）。

为什么单独一个文件：`seed_demo.py` 的职责是"造像真的一样的假数据"，不该掺入
真实内容导入。这里只做一件事——把已经落盘的**真实**视频/文章连同转录、分析、
拆解、封面写进演示库，让演示站里的素材是真货。

数据来源（`data/demo_pipeline/real_pack.json`，由 export_real_pack.py 从 Postgres 导出）：
- 视频/文章元数据、真实转录（faster-whisper）、真实分析（deepseek-v4.1-flash-free）、
  真实爆款拆解；封面从 `data/cover_cache/<asset_id>.bin` 复制。
缺文件时安静跳过，可离线运行。
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.models import (
    AnalysisResult,
    ContentItem,
    MediaAsset,
    PlatformAccount,
    Transcript,
    ViralBreakdown,
)

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = Path(os.environ.get("DEMO_REAL_DIR", str(ROOT / "data" / "demo_pipeline")))
STORAGE = ROOT / "data" / "demo_storage"
COVER_CACHE = ROOT / "data" / "cover_cache"

_WECHAT = "wechat_mp"

# 超大视频不入演示（用户要求：超大视频跳过也不上演示）
MAX_VIDEO_BYTES = int(os.environ.get("DEMO_MAX_VIDEO_MB", "20")) * 1024 * 1024


def _load_pack():
    p = BUILD_DIR / "real_pack.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text("utf-8"))


def _owner(session, platform_key: str):
    """给真实内容挂一个演示账号（文章用微信系统账号占位）。"""
    return session.scalars(
        select(PlatformAccount).where(PlatformAccount.platform == platform_key)
    ).first()


def _to_breakdown_sections(item: dict) -> dict:
    """把分析产物映射到爆款拆解报告实际渲染的 sections 结构。

    Assets.vue 读取的键：hook / structure / pacing / script /
    psych_triggers / cta / blueprint / replication_tips。
    缺的字段留空，前端会跳过该板块（不编造）。
    """
    raw = item.get("sections") or {}
    structured = item.get("structured") or {}
    if isinstance(structured, str):
        try:
            structured = json.loads(structured)
        except (TypeError, ValueError):
            structured = {}

    hook = {
        "first_line": (item.get("full_text") or "").strip()[:60],
        "first_frame": "",
        "hook_type": raw.get("hook_type") or "",
        "why": structured.get("hook") or "",
    }

    structure = []
    for row in raw.get("structure") or []:
        if not isinstance(row, dict):
            continue
        structure.append(
            {
                "segment": row.get("stage") or "",
                "role": row.get("stage") or "",
                "summary": " ".join(
                    x for x in (row.get("what"), row.get("why")) if x
                ).strip(),
            }
        )

    factors = list(raw.get("viral_factors") or [])
    if not factors and structured.get("highlights"):
        factors = list(structured["highlights"])

    sections: dict = {"hook": hook}
    if structure:
        sections["structure"] = structure
    if factors:
        sections["psych_triggers"] = factors
    formula = raw.get("reusable_formula") or ""
    if formula:
        sections["blueprint"] = [formula]
    risk = raw.get("risk") or ""
    if risk:
        sections["replication_tips"] = [risk]
    return sections


def _data_context(item: dict) -> dict:
    """后端确定性计算的互动上下文；演示数据缺指标时留空对象（前端显示 —）。"""
    metrics = item.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    out = {}
    for src, dst in (
        ("play_count", "play"),
        ("like_count", "like"),
        ("comment_count", "comment"),
    ):
        if metrics.get(src) is not None:
            out[dst] = metrics[src]
    return out


def inject(session) -> dict[str, int]:
    """把真实内容写进演示库；返回写入统计。文件缺失时原样返回零。"""
    pack = _load_pack()
    stats = {"real_video": 0, "real_article": 0, "real_analysis": 0,
             "real_breakdown": 0, "covers": 0, "skipped_large": 0}
    if not pack:
        return stats

    fallback_account = session.scalars(select(PlatformAccount)).first()
    fallback_id = fallback_account.id if fallback_account else None
    cover_map: dict[int, int] = {}
    media_out = STORAGE.parent / "demo_covers"
    media_out.mkdir(parents=True, exist_ok=True)

    for item in pack.get("items", []):
        is_article = item.get("content_type") == "article"
        ext = item.get("external_id") or ""
        if not ext:
            continue
        # 超大视频直接跳过：源文件太大，不适合放进静态演示站
        if not is_article and item.get("video_file"):
            src = STORAGE / (item.get("relative_path") or "") / item["video_file"]
            if src.is_file() and src.stat().st_size > MAX_VIDEO_BYTES:
                stats["skipped_large"] = stats.get("skipped_large", 0) + 1
                continue
        existing = session.scalars(
            select(ContentItem).where(ContentItem.external_id == ext)
        ).first()
        if existing:
            continue
        account = _owner(session, item.get("platform") or "") or fallback_account
        content = ContentItem(
            account_id=account.id if account else fallback_id,
            platform=item.get("platform") or "douyin",
            external_id=ext,
            content_type=item.get("content_type") or "video",
            title=item.get("title") or "",
            author_name=item.get("author_name") or "",
            author_id=item.get("author_id") or "",
            url=item.get("url") or "",
            cover_url=item.get("cover_url") or "",
            published_at=datetime.utcnow(),
            duration_sec=int(item.get("duration_sec") or 0),
            auto_category=item.get("auto_category") or "",
            category_confidence=float(item.get("category_confidence") or 0.8),
            source_kind="crawl" if not is_article else "import",
            source_id=None,
            extra={"demo": True, "real": True},
        )
        session.add(content)
        session.flush()

        has_video = bool(item.get("video_file"))
        asset = MediaAsset(
            content_id=content.id,
            storage_root_id=None,
            relative_path=item.get("relative_path") or f"demo/{content.platform}/{ext}",
            video_file=item.get("video_file") or "",
            audio_file="",
            download_status="done",
            transcribe_status="done" if item.get("full_text") else "pending",
            analyze_status="done" if item.get("summary") else "pending",
            process_tier="advanced" if item.get("verdict") else ("basic" if item.get("full_text") else ""),
            understand_status="",
        )
        session.add(asset)
        session.flush()

        if item.get("full_text"):
            t = Transcript(asset_id=asset.id)
            t.language = item.get("language") or "zh"
            t.model = item.get("tmodel") or ("wechat_import" if is_article else "faster-whisper-small")
            t.segments = item.get("segments") or []
            t.full_text = item["full_text"]
            session.add(t)

        if item.get("summary"):
            r = AnalysisResult(asset_id=asset.id)
            r.provider = "qwen"
            r.model = "deepseek-v4.1-flash-free"
            r.status = "done"
            r.summary = item["summary"]
            r.keywords = item.get("tags") or "[]"
            r.result = item.get("structured") or "{}"
            session.add(r)
            stats["real_analysis"] += 1

        if item.get("verdict"):
            b = ViralBreakdown(asset_id=asset.id)
            b.status = "done"
            b.score = float(item.get("score") or 87.0)
            b.verdict = item.get("verdict") or ""
            # 把分析产物映射到产品实际渲染的 sections 结构
            # （Assets.vue 读 hook/structure/pacing/script/psych_triggers/cta/blueprint/replication_tips）
            b.sections = _to_breakdown_sections(item)
            b.comment_insights = item.get("comment_insights") or {}
            b.data_context = _data_context(item)
            b.understanding_available = False
            b.model = "deepseek-v4.1-flash-free"
            b.prompt_version = "v1"
            b.usage = {}
            b.routing = {}
            session.add(b)
            stats["real_breakdown"] += 1

        src_aid = item.get("src_asset_id")
        if src_aid:
            cover_map[asset.id] = int(src_aid)
            src = COVER_CACHE / f"{src_aid}.bin"
            if src.is_file() and src.stat().st_size > 3000:
                # 按演示库的新 asset_id 落进封面缓存，导出流程会自动复制
                COVER_CACHE.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, COVER_CACHE / f"{asset.id}.bin")
                (COVER_CACHE / f"{asset.id}.json").write_text(
                    '{"content_type": "image/png"}', "utf-8"
                )
                stats["covers"] += 1
        stats["real_article" if is_article else "real_video"] += 1
    session.flush()
    return stats


def stage_covers(asset_ids, out_dir: Path) -> int:
    """把 cover_cache 里对应的封面复制到演示媒体目录；返回复制数量。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for aid in asset_ids:
        src = COVER_CACHE / f"{aid}.bin"
        if src.is_file() and src.stat().st_size > 3000:
            shutil.copy2(src, out_dir / f"{aid}.png")
            n += 1
    return n
