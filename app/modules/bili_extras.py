"""B站媒体库增强：字幕（SRT）、弹幕（XML）、NFO 元数据。

对齐 bili-sync（MIT）的媒体库体验：下载视频时同时产出 Emby/Jellyfin 可识别的
字幕、弹幕与 NFO。接口调用沿用本仓库的限速与 Cookie 约定，不复制上游代码。
"""

from __future__ import annotations

import html
import re
from pathlib import Path

_PLAYER_V2 = "https://api.bilibili.com/x/player/wbi/v2"
_DANMAKU_SEG = "https://api.bilibili.com/x/v2/dm/web/seg.so"


def _srt_time(seconds: float) -> str:
    """秒 -> SRT 时间戳 ``HH:MM:SS,mmm``。"""
    seconds = max(0.0, float(seconds or 0))
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis >= 1000:
        whole += 1
        millis -= 1000
    hh, rem = divmod(whole, 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{millis:03d}"


def build_srt(body: list[dict]) -> str:
    """字幕 JSON body -> SRT 文本。"""
    lines: list[str] = []
    for idx, item in enumerate(body or [], start=1):
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        lines.append(str(idx))
        lines.append(f"{_srt_time(item.get('from'))} --> {_srt_time(item.get('to'))}")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


_DANMAKU_RE = re.compile(r'<d p="([^"]*)"[^>]*>(.*?)</d>', re.DOTALL)


def build_danmaku_xml(raw_xml: str) -> str:
    """把 B站旧版弹幕 XML 归一为标准弹幕 XML（时间保留三位小数）。"""
    entries: list[str] = []
    for match in _DANMAKU_RE.finditer(raw_xml or ""):
        attrs = match.group(1)
        text = html.escape(match.group(2) or "", quote=False)
        parts = attrs.split(",")
        try:
            parts[0] = f"{float(parts[0]):.3f}"
        except (IndexError, ValueError):
            pass
        entries.append(f'<d p="{",".join(parts)}">{text}</d>')
    body = "".join(entries)
    return f'<?xml version="1.0" encoding="UTF-8"?><i>{body}</i>'


def build_nfo(
    *,
    title: str,
    plot: str = "",
    author: str = "",
    published_at: str | None = None,
    bvid: str = "",
    tags: list[str] | None = None,
) -> str:
    """生成 Emby/Jellyfin 可识别的 movie NFO。"""
    def esc(v: str) -> str:
        return html.escape(str(v or ""), quote=False)

    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<movie>", f"  <title>{esc(title)}</title>"]
    if plot:
        parts.append(f"  <plot>{esc(plot)}</plot>")
    if author:
        parts.append(f"  <studio>{esc(author)}</studio>")
    if published_at:
        parts.append(f"  <premiered>{esc(str(published_at)[:10])}</premiered>")
    if bvid:
        parts.append(f'  <uniqueid type="bilibili">{esc(bvid)}</uniqueid>')
    for tag in tags or []:
        if str(tag).strip():
            parts.append(f"  <tag>{esc(tag)}</tag>")
    parts.append("</movie>")
    return "\n".join(parts) + "\n"


def _http(credential: dict):
    from app.modules.bilibili import _cookies, _http as bili_http

    return bili_http()


async def fetch_subtitles(credential: dict, bvid: str, cid: int, out_dir: Path) -> list[str]:
    """抓取非 AI 字幕并写入 ``<bvid>.<lan>.srt``，返回写入的文件名。"""
    from app.modules.bilibili import _cookies

    written: list[str] = []
    async with _http(credential) as client:
        resp = await client.get(_PLAYER_V2, params={"bvid": bvid, "cid": cid}, cookies=_cookies(credential))
        data = (resp.json() or {}).get("data") or {}
        subtitles = ((data.get("subtitle") or {}).get("subtitles") or [])
        for info in subtitles:
            url = str(info.get("subtitle_url") or "")
            if "ai_subtitle" in url:  # AI 字幕过滤，与 bili-sync 行为一致
                continue
            lan = str(info.get("lan") or "und")
            if url.startswith("//"):
                url = "https:" + url
            sub = await client.get(url)
            body = (sub.json() or {}).get("body") or []
            text = build_srt(body)
            if not text.strip():
                continue
            target = Path(out_dir) / f"{bvid}.{lan}.srt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            written.append(target.name)
    return written


async def fetch_danmaku(credential: dict, bvid: str, cid: int, duration: int, out_dir: Path) -> str:
    """抓取弹幕并写入 ``<bvid>.xml``，返回文件名（无弹幕返回空串）。"""
    from app.modules.bilibili import _cookies

    segments = max(1, (int(duration or 0) + 359) // 360)
    chunks: list[str] = []
    async with _http(credential) as client:
        for idx in range(1, segments + 1):
            resp = await client.get(
                _DANMAKU_SEG,
                params={"type": 1, "oid": cid, "segment_index": idx},
                cookies=_cookies(credential),
            )
            content_type = (resp.headers.get("content-type") or "")
            if "octet-stream" not in content_type:
                continue
            chunks.append(resp.content.decode("utf-8", errors="ignore"))
    xml = build_danmaku_xml("".join(chunks))
    if "<d " not in xml:
        return ""
    target = Path(out_dir) / f"{bvid}.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(xml, encoding="utf-8")
    return target.name


def write_nfo(out_dir: Path, bvid: str, **kwargs) -> str:
    """写入 ``<bvid>.nfo``，返回文件名。"""
    target = Path(out_dir) / f"{bvid}.nfo"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_nfo(**kwargs), encoding="utf-8")
    return target.name
