"""把「抖小云」已下载的抖音素材目录导入平台素材库（复用已有文件，不重下）。

背景
----
抖小云（dysync.net）在 NAS 上把抖音素材落在：
    <根>/<博主>/<标题>/<aweme_id>.mp4
以及同目录的旁挂文件（同前缀 <aweme_id>）：
    .nfo            Kodi 元数据（title / releasedate / genre / actor）
    -poster.jpg     封面
    .actors/        博主头像 <博主>.jpg
    .全文.txt       转录全文（无标点，whisper 产物）
    .字幕.srt       带时间轴字幕（部分内容才有）
    .屏幕字.txt     OCR 屏幕字
    .转写.md        人工整理版转写（含时长/发布时间/话题）
    .拆解.md        人工爆款拆解卡

平台自己的下载产物同名规则是「标题_ID」+ 子目录模板，与抖小云不同，所以历史文件
**不能**被平台自动识别。本脚本做的是「导入」：为已有文件补建
ContentItem / MediaAsset / Transcript / ViralBreakdown / 封面缓存 等数据库行，
让素材库能看到、能播放、能检索——不复制、不移动、不重新下载视频。

用法（在 api 容器内执行）：
    python scripts/import_douyin_archive.py --root /mnt/douyin-archive \
        --storage-root-id 1 --account-id 1 [--limit 20] [--apply]

不带 --apply 只扫描并打印统计（干跑）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import (
    AnalysisResult,
    ContentItem,
    CreatorProfile,
    MediaAsset,
    PlatformAccount,
    StorageRoot,
    Transcript,
    ViralBreakdown,
)

_VIDEO_EXT = ".mp4"
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DUR_RE = re.compile(r"时长：\s*(\d{2}):(\d{2})")
_TAG_RE = re.compile(r"话题：\s*(\[.*\])")
_TYPE_RE = re.compile(r"类型判定：\s*\*\*(.+?)\*\*")
_HOOK_TYPE_RE = re.compile(r"钩子类型：\s*(.+)")


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _nfo_fields(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        # 抖小云写的是 utf-8（带 BOM），用 utf-8-sig 兼容
        root = ElementTree.fromstring(path.read_bytes().decode("utf-8-sig", errors="replace"))
    except (ElementTree.ParseError, OSError):
        return {}
    out: dict = {"genres": []}
    for child in root:
        if child.tag == "title":
            out["title"] = (child.text or "").strip()
        elif child.tag in ("releasedate", "premiered") and "date" not in out:
            out["date"] = (child.text or "").strip()
        elif child.tag == "genre":
            out["genres"].append((child.text or "").strip())
    return out


def _parse_date(text: str) -> datetime | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _parse_duration(text: str) -> int:
    m = _DUR_RE.search(text or "")
    if not m:
        return 0
    return int(m.group(1)) * 60 + int(m.group(2))


def _srt_segments(path: Path) -> tuple[list, str]:
    """极简 SRT 解析：返回 (segments, full_text)。"""
    if not path.is_file():
        return [], ""
    raw = _read(path)
    segs: list = []
    full: list[str] = []
    time_re = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})")
    blocks = re.split(r"\n\s*\n", raw)
    for block in blocks:
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        m = None
        text_lines: list[str] = []
        for ln in lines:
            mm = time_re.search(ln)
            if mm:
                m = mm
            elif not ln.isdigit():
                text_lines.append(ln)
        if m and text_lines:
            start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
            end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
            txt = " ".join(text_lines)
            segs.append({"start": round(start, 2), "end": round(end, 2), "text": txt})
            full.append(txt)
    return segs, " ".join(full)


def _breakdown_sections(md: str) -> dict:
    """从人工拆解卡里取平台能渲染的字段（取不到就留空，不编造）。"""
    sections: dict = {}
    hook_type = _HOOK_TYPE_RE.search(md)
    if hook_type:
        sections["hook"] = {"hook_type": hook_type.group(1).strip(), "first_line": "", "why": ""}
    return sections


def _cache_cover(asset_id: int, poster: Path) -> None:
    """把本地海报写入平台的封面缓存（/api/assets/{id}/cover 直接命中）。"""
    try:
        cache = Path(settings.data_dir) / "cover_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{asset_id}.bin").write_bytes(poster.read_bytes())
        (cache / f"{asset_id}.json").write_text(
            json.dumps({"file": f"{asset_id}.bin", "type": "image/jpeg"}), "utf-8"
        )
    except OSError:
        pass


def _upsert_creator(db, name: str, avatar_file: Path | None) -> str:
    """博主档案：author_id 用博主目录名（稳定、唯一），头像存 data 目录。"""
    author_id = name
    cp = db.scalar(
        select(CreatorProfile).where(
            CreatorProfile.platform == "douyin", CreatorProfile.author_id == author_id
        )
    )
    avatar_url = ""
    if avatar_file and avatar_file.is_file():
        try:
            dst_dir = Path(settings.data_dir) / "archive_avatars"
            dst_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w\-.一-鿿]+", "_", name) or "creator"
            dst = dst_dir / f"dy_{safe}.jpg"
            if not dst.exists():
                dst.write_bytes(avatar_file.read_bytes())
            avatar_url = f"/api/archive/avatar/{dst.name}"
        except OSError:
            avatar_url = ""
    if cp is None:
        cp = CreatorProfile(platform="douyin", author_id=author_id, author_name=name, avatar=avatar_url)
        db.add(cp)
    else:
        if name:
            cp.author_name = name
        if avatar_url:
            cp.avatar = avatar_url
    return author_id


def _iter_video_dirs(root: Path):
    """产出 (相对目录, 该目录下的 mp4 列表)。"""
    for dirpath, _dirnames, filenames in os.walk(root):
        mp4s = [f for f in filenames if f.lower().endswith(_VIDEO_EXT)]
        if mp4s:
            d = Path(dirpath)
            yield d.relative_to(root), sorted(mp4s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/mnt/douyin-archive")
    ap.add_argument("--storage-root-id", type=int, required=True)
    ap.add_argument("--account-id", type=int, required=True)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个视频（0=全部）")
    ap.add_argument("--apply", action="store_true", help="真正写库；缺省为干跑统计")
    ap.add_argument("--commit-every", type=int, default=200)
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"目录不存在：{root}")
        return 2

    db = SessionLocal()
    try:
        account = db.get(PlatformAccount, args.account_id)
        sroot = db.get(StorageRoot, args.storage_root_id)
        if account is None or sroot is None:
            print("账号或存储根不存在")
            return 2
        if account.platform != "douyin":
            print(f"警告：账号 {account.id} 平台是 {account.platform}，非抖音")

        existing = set(
            db.scalars(
                select(ContentItem.external_id).where(
                    ContentItem.platform == "douyin",
                    ContentItem.account_id == args.account_id,
                )
            ).all()
        )

        stats = dict(dirs=0, videos=0, imported=0, skipped=0, no_mp4=0, with_transcript=0,
                     with_breakdown=0, with_cover=0, errors=0)
        pending = 0
        for rel_dir, mp4s in _iter_video_dirs(root):
            stats["dirs"] += 1
            creator = rel_dir.parts[0] if rel_dir.parts else ""
            actors = root / rel_dir / ".actors"
            avatar = None
            if actors.is_dir():
                for f in actors.iterdir():
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                        avatar = f
                        break
            for mp4 in mp4s:
                stats["videos"] += 1
                if args.limit and stats["videos"] > args.limit:
                    break
                aweme_id = Path(mp4).stem
                if not aweme_id.isdigit():
                    stats["no_mp4"] += 1
                    continue
                if aweme_id in existing:
                    stats["skipped"] += 1
                    continue

                vdir = root / rel_dir
                nfo = _nfo_fields(vdir / f"{aweme_id}.nfo")
                zhuanxie = _read(vdir / f"{aweme_id}.转写.md")
                full_txt = _read(vdir / f"{aweme_id}.全文.txt").strip()
                jiexi = _read(vdir / f"{aweme_id}.拆解.md")
                poster = vdir / f"{aweme_id}-poster.jpg"
                srt = vdir / f"{aweme_id}.字幕.srt"

                title = nfo.get("title") or ""
                if not title:
                    m = re.search(r"^#\s+(.+)$", zhuanxie, re.M)
                    title = m.group(1).strip() if m else rel_dir.parts[-1]
                published = _parse_date(nfo.get("date", "")) or _parse_date(zhuanxie)
                duration = _parse_duration(zhuanxie)
                tags = []
                tm = _TAG_RE.search(zhuanxie)
                if tm:
                    try:
                        tags = json.loads(tm.group(1))
                    except ValueError:
                        tags = []

                if not args.apply:
                    stats["imported"] += 1
                    if full_txt:
                        stats["with_transcript"] += 1
                    if jiexi:
                        stats["with_breakdown"] += 1
                    if poster.is_file():
                        stats["with_cover"] += 1
                    continue

                try:
                    author_id = _upsert_creator(db, creator, avatar)
                    content = ContentItem(
                        account_id=args.account_id,
                        platform="douyin",
                        external_id=aweme_id,
                        content_type="video",
                        title=title[:512],
                        author_name=creator[:128],
                        author_id=author_id[:128],
                        url=f"https://www.douyin.com/video/{aweme_id}",
                        cover_url="",  # 用本地缓存封面，不依赖会过期的 CDN
                        published_at=published,
                        duration_sec=duration,
                        source_kind="archive",
                        extra={"imported_archive": True, "source_dir": str(rel_dir),
                               "tags": tags, "genres": nfo.get("genres", [])},
                    )
                    db.add(content)
                    db.flush()

                    asset = MediaAsset(
                        content_id=content.id,
                        storage_root_id=args.storage_root_id,
                        relative_path=str(rel_dir),
                        video_file=mp4,
                        download_status="done",
                        transcribe_status="done" if (full_txt or srt.is_file()) else "pending",
                        analyze_status="pending",
                    )
                    db.add(asset)
                    db.flush()

                    if full_txt:
                        segs, srt_full = _srt_segments(srt)
                        text = full_txt or srt_full
                        db.add(Transcript(asset_id=asset.id, language="zh", model="archive",
                                          segments=segs, full_text=text))
                        stats["with_transcript"] += 1

                    if jiexi:
                        verdict = ""
                        vm = re.search(r"类型判定：\*\*(.+?)\*\*", jiexi)
                        if vm:
                            verdict = f"人工拆解卡导入 · 类型判定：{vm.group(1).strip()}"
                        db.add(ViralBreakdown(
                            asset_id=asset.id, score=0, verdict=verdict,
                            sections=_breakdown_sections(jiexi),
                            status="done", model="archive", prompt_version="archive-import",
                        ))
                        stats["with_breakdown"] += 1

                    if poster.is_file():
                        if asset.id is not None:
                            _cache_cover(asset.id, poster)
                        stats["with_cover"] += 1

                    existing.add(aweme_id)
                    stats["imported"] += 1
                except Exception as exc:  # noqa: BLE001 单条失败不该中断整批
                    db.rollback()
                    stats["errors"] += 1
                    print(f"  ! {aweme_id}: {exc}")
                    continue

                pending += 1
                if pending >= args.commit_every:
                    db.commit()
                    pending = 0
                    print(f"  已导入 {stats['imported']} …")
            if args.limit and stats["videos"] >= args.limit:
                break

        if args.apply and pending:
            db.commit()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        if not args.apply:
            print("（干跑统计，未写库；加 --apply 才真正导入）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
