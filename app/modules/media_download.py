"""通用媒体下载：yt-dlp 安全封装。协议来源：yt-dlp（Unlicense）。

安全红线（见开发总纲 §3.9）：默认禁用插件、自更新、ExecPP、file:// URL、
任意外部下载器参数。只暴露受控的高层选项：目标 URL、输出目录、是否仅音频。
外部 API/skill 不得提交绝对输出路径或任意 yt-dlp 参数。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DownloadResult:
    ok: bool
    video_file: str = ""  # 相对输出目录的文件名
    audio_file: str = ""
    title: str = ""
    duration: int = 0
    message: str = ""


# yt-dlp 安全基线选项：关掉一切危险开关
_SAFE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "no_color": True,
    "ignoreconfig": True,  # 不读用户 yt-dlp 配置
    "no_check_certificates": False,
    "allow_unplayable_formats": False,
    "enable_file_urls": False,  # 禁 file:// URL
    "geo_bypass": False,
}


def _build_opts(out_dir: Path, audio_only: bool, cookie: str = "") -> dict:
    opts = dict(_SAFE_OPTS)
    opts["outtmpl"] = str(out_dir / "%(id)s.%(ext)s")
    opts["paths"] = {"home": str(out_dir)}
    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "wav", "preferredquality": "192"}
        ]
    else:
        opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        opts["merge_output_format"] = "mp4"
    if cookie:
        opts["http_headers"] = {"Cookie": cookie}
    return opts


def _run_download(url: str, out_dir: Path, audio_only: bool, cookie: str) -> DownloadResult:
    try:
        import yt_dlp
    except ImportError:
        return DownloadResult(ok=False, message="yt-dlp 未安装（uv sync 时应已包含）")

    out_dir.mkdir(parents=True, exist_ok=True)
    opts = _build_opts(out_dir, audio_only, cookie)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # yt-dlp 内部异常种类繁多，统一兜住
        return DownloadResult(ok=False, message=f"下载失败: {exc}")

    vid = info.get("id", "")
    title = info.get("title", "")
    duration = int(info.get("duration") or 0)
    # 找出实际产物文件名
    produced = sorted(p.name for p in out_dir.glob(f"{vid}.*"))
    video_file = next((n for n in produced if n.endswith(".mp4")), "")
    audio_file = next((n for n in produced if n.endswith((".wav", ".m4a", ".mp3"))), "")
    if audio_only and not audio_file:
        audio_file = produced[0] if produced else ""
    if not audio_only and not video_file:
        video_file = produced[0] if produced else ""
    return DownloadResult(
        ok=bool(produced),
        video_file=video_file,
        audio_file=audio_file,
        title=title,
        duration=duration,
        message="下载完成" if produced else "未产出文件",
    )


async def download(url: str, out_dir: Path, audio_only: bool = False, cookie: str = "") -> DownloadResult:
    """异步包装：yt-dlp 是同步阻塞的，丢到线程池执行。"""
    return await asyncio.to_thread(_run_download, url, out_dir, audio_only, cookie)

async def download_direct(url: str, out_dir: Path, cookie: str = "") -> DownloadResult:
    """直链下载（照抄抖小云 TryDownloadOnceAsync）。

    抖小云抓取时就把 `video.play_addr` 的直链存下来，下载阶段只做一次带 Cookie 的
    HTTP GET，不依赖 yt-dlp（抖音页面下载要求 fresh cookies，很脆）。
    这里同样处理：流式落盘、按 Content-Type 修正扩展名、失败退回重试交给上层。
    """
    import httpx

    out_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": _UA_FOR_DOWNLOAD, "Referer": "https://www.douyin.com/"}
    if cookie:
        headers["Cookie"] = cookie
    # 直链里可能带 a=1 之类参数，文件名用 aweme id 兜底
    name = _guess_name(url)
    target = out_dir / f"{name}.mp4"
    try:
        async with httpx.AsyncClient(headers=headers, timeout=300, follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                if r.status_code != 200:
                    return DownloadResult(ok=False, message=f"直链下载失败：HTTP {r.status_code}")
                ctype = (r.headers.get("content-type") or "").lower()
                if "video/mp4" in ctype or "octet-stream" in ctype or not ctype:
                    target = out_dir / f"{name}.mp4"
                elif "audio" in ctype:
                    target = out_dir / f"{name}.m4a"
                written = 0
                with open(target, "wb") as fh:
                    async for chunk in r.aiter_bytes(64 * 1024):
                        fh.write(chunk)
                        written += len(chunk)
    except Exception as exc:  # noqa: BLE001 网络异常种类多，统一兜成可读错误
        return DownloadResult(ok=False, message=f"直链下载失败: {exc}")
    if written <= 0:
        return DownloadResult(ok=False, message="直链下载失败：未写入任何数据")
    produced = target.name
    return DownloadResult(ok=True, video_file=produced, message="下载完成")


def _guess_name(url: str) -> str:
    """从直链里挑一个稳定文件名：优先 aweme/video id，否则用哈希。"""
    import hashlib
    import re

    m = re.search(r"/(?:video|aweme)/?(\d{10,})", url)
    if m:
        return m.group(1)
    # 抖音 play_addr 直链形如 .../play/?video_id=v0200xxx&ratio=1080p
    for pat in (r"[?&]video_id=(v?[0-9a-zA-Z]+)", r"[?&]vid=([0-9a-zA-Z]+)"):
        m2 = re.search(pat, url)
        if m2:
            return m2.group(1)
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


_UA_FOR_DOWNLOAD = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
)
