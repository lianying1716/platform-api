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
