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


def _as_dir(out_dir) -> Path | None:
    """统一把输出目录规整成 Path；空目录返回 None（调用方需判空）。

    ``resolve_asset_dir`` 返回的是 str（storage_root + relative_path 拼出来的），
    而本模块按 Path 使用（``.mkdir()`` / ``.glob()``）。在边界处归一化，
    避免 ``'str' object has no attribute 'mkdir'`` 这类只在真实下载时才炸的错。
    注意 ``Path("")`` 会变成 ``"."``，所以空值必须在这里就挡掉。
    """
    if out_dir is None:
        return None
    if isinstance(out_dir, Path):
        return out_dir
    text = str(out_dir).strip()
    return Path(text) if text else None


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

    out_dir = _as_dir(out_dir)
    if out_dir is None:
        return DownloadResult(ok=False, message="未指定下载目录（存储根未配置）")
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


async def download(url: str, out_dir: "Path | str", audio_only: bool = False, cookie: str = "") -> DownloadResult:
    """异步包装：yt-dlp 是同步阻塞的，丢到线程池执行。

    ``out_dir`` 允许是 str（``resolve_asset_dir`` 的返回值），内部会归一化成 Path。
    """
    return await asyncio.to_thread(_run_download, url, out_dir, audio_only, cookie)

async def download_direct(
    url: str,
    out_dir: "Path | str",
    cookie: str = "",
    *,
    mirror_urls: list[str] | None = None,
    retries: int = 3,
    name: str = "",
) -> DownloadResult:
    """直链下载（照抄抖小云 TryDownloadOnceAsync + DownloadAsync 的镜像轮换重试）。

    抖小云抓取时就把 `video.play_addr` 的直链存下来，下载阶段只做一次带 Cookie 的
    HTTP GET，不依赖 yt-dlp（抖音页面下载要求 fresh cookies，很脆）。

    抖音直链带 ``sign=`` 签名，会过期、也可能被 CDN 限流。抖小云的做法是失败后
    轮换同一视频的其它镜像 URL 并把重试延迟翻倍；这里照做：
    ``mirror_urls`` 给出全部候选地址，逐条尝试，全部失败才判失败。
    """
    import httpx

    out_dir = _as_dir(out_dir)
    if out_dir is None:
        return DownloadResult(ok=False, message="未指定下载目录（存储根未配置）")
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": _UA_FOR_DOWNLOAD, "Referer": "https://www.douyin.com/"}
    if cookie:
        headers["Cookie"] = cookie
    # 文件名：优先调用方给的「标题_内容ID」（照抄抖小云 SanitizeLinuxFileName(desc, awemeId)），
    # 没给才从直链里挑 aweme/video id 兜底。
    name = _safe_name(name) if (name or "").strip() else _guess_name(url)

    candidates: list[str] = []
    for u in [url, *(mirror_urls or [])]:
        if isinstance(u, str) and u.strip() and u not in candidates:
            candidates.append(u)
    if not candidates:
        return DownloadResult(ok=False, message="直链下载失败：没有可用的下载地址")

    last_message = ""
    attempts = max(1, min(retries, len(candidates)))
    for idx in range(attempts):
        candidate = candidates[idx]
        try:
            result = await _download_one(candidate, out_dir, name, headers)
        except Exception as exc:  # noqa: BLE001 网络异常种类多，统一兜成可读错误
            result = DownloadResult(ok=False, message=f"直链下载失败: {exc}")
        if result.ok:
            return result
        last_message = result.message
        # 重试前退避（保守档：3s、6s、12s…，上限 30s）。
        # 抖小云是 1→2→4s；我们把起点和上限都抬高，失败时不要急促重试。
        if idx < attempts - 1:
            await asyncio.sleep(min(3.0 * (2 ** idx), 30.0))
    return DownloadResult(ok=False, message=last_message or "直链下载失败")


async def _download_one(url: str, out_dir: Path, name: str, headers: dict) -> DownloadResult:
    """单次直链下载尝试：流式落盘、按 Content-Type 修正扩展名。"""
    import httpx

    target = out_dir / f"{name}.mp4"
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
    if written <= 0:
        return DownloadResult(ok=False, message="直链下载失败：未写入任何数据")
    return DownloadResult(ok=True, video_file=target.name, message="下载完成")


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


# ---------------------------------------------------------------------------
# DASH 下载（B站）：视频/音频分离，多 CDN 轮换 + Range 分片并行 + ffmpeg 合并。
# 协议来源：bili-sync（MIT）downloader.rs —— multi_fetch / multi_fetch_and_merge /
# fetch_parallel。B站 CDN 要求 User-Agent + Referer（缺任一个直接 403），
# 且每个流有多个 backupUrl，主地址失败必须轮换。
# ---------------------------------------------------------------------------

_UA_BILIBILI = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _bili_headers(cookie: str = "") -> dict:
    headers = {"User-Agent": _UA_BILIBILI, "Referer": "https://www.bilibili.com/"}
    if cookie:
        headers["Cookie"] = cookie
    return headers


async def _fetch_to_file(
    client,
    urls: list[str],
    target: Path,
    headers: dict,
    *,
    is_stream: bool = True,
    parallel_above: int = 20 * 1024 * 1024,  # 保守档：对齐 bili-sync 默认阈值
    concurrency: int = 2,                    # 保守档：bili-sync 默认 4，我们减半
) -> DownloadResult:
    """把一组候选 URL 依次尝试，任一成功即落盘 target（照抄 bili-sync multi_fetch_internal）。

    is_stream=True 时用 Range GET 探测是否支持分片（B站流 HEAD 常 404 但 GET 正常），
    文件够大则并行分片；否则串行流式下载。大文件并行能显著提速且更抗 CDN 限速。
    """
    if not urls:
        return DownloadResult(ok=False, message="没有可用的下载地址")
    last = ""
    for idx, url in enumerate(urls):
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = await _probe_size(client, url, headers, is_stream)
            if size and size >= parallel_above:
                await _fetch_parallel(client, url, target, headers, size, concurrency)
            else:
                await _fetch_serial(client, url, target, headers)
            if target.exists() and target.stat().st_size > 0:
                return DownloadResult(ok=True, video_file=target.name, message="下载完成")
            last = "未写入任何数据"
        except Exception as exc:  # noqa: BLE001 单条地址失败换下一个备份
            last = f"{type(exc).__name__}: {exc}"
            if idx == len(urls) - 1:
                break
    target.unlink(missing_ok=True)
    return DownloadResult(ok=False, message=f"全部 {len(urls)} 个地址均失败：{last}")


async def _probe_size(client, url: str, headers: dict, is_stream: bool) -> int:
    """探测文件大小；不支持 Range/未返回长度时返回 0（走串行）。"""
    if is_stream:
        r = await client.get(url, headers={**headers, "Range": "bytes=0-0"})
        if r.status_code != 206:
            return 0
        cr = r.headers.get("content-range") or ""
        if "/" in cr:
            try:
                return int(cr.rsplit("/", 1)[1])
            except ValueError:
                return 0
        return 0
    r = await client.head(url, headers=headers)
    if r.status_code != 200:
        return 0
    ar = (r.headers.get("accept-ranges") or "none").lower()
    if ar == "none":
        return 0
    try:
        return int(r.headers.get("content-length") or 0)
    except ValueError:
        return 0


# 流空闲超时：多久没收到新数据就判定卡死（照抄抖小云 TryDownloadOnceAsync 的
# streamTimeout —— 它默认 60s，避免连接挂着但永远不结束）。
_STREAM_STALL_SECONDS = 60.0


async def _fetch_serial(client, url: str, target: Path, headers: dict) -> None:
    """串行流式下载 + 长度校验 + 空闲超时。

    - 长度校验照抄 bili-sync fetch_serial（收到的字节必须等于 Content-Length）；
    - 空闲超时照抄抖小云 streamTimeout：写出一个 _STREAM_STALL_SECONDS 计时器，
      每收到一段数据就重置；计时器先触发说明 CDN 卡住，直接失败换备份地址。
    """
    async with client.stream("GET", url, headers=headers) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        expected = int(r.headers.get("content-length") or 0)
        written = 0
        stall = asyncio.get_running_loop().time() + _STREAM_STALL_SECONDS
        with open(target, "wb") as fh:
            it = r.aiter_bytes(256 * 1024).__aiter__()
            while True:
                timeout = max(0.1, stall - asyncio.get_running_loop().time())
                try:
                    chunk = await asyncio.wait_for(it.__anext__(), timeout=timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(f"流读取超时（{_STREAM_STALL_SECONDS:.0f}秒无数据）") from exc
                fh.write(chunk)
                written += len(chunk)
                stall = asyncio.get_running_loop().time() + _STREAM_STALL_SECONDS
    if expected and written != expected:
        raise RuntimeError(f"字节数不符：期望 {expected} 实收 {written}")


async def _fetch_parallel(
    client, url: str, target: Path, headers: dict, size: int, concurrency: int
) -> None:
    """Range 分片并行下载（照抄 bili-sync fetch_parallel）。

    按 concurrency 等分总长度，各片独立并发写同一文件的对应偏移；
    每片校验实收字节数。任何一片失败即整体失败，由上层轮换备份地址。
    """
    chunk = size // concurrency
    with open(target, "wb") as fh:
        fh.truncate(size)

    async def _one(idx: int) -> None:
        start = idx * chunk
        end = size - 1 if idx == concurrency - 1 else start + chunk - 1
        r = await client.get(url, headers={**headers, "Range": f"bytes={start}-{end}"})
        if r.status_code != 206:
            raise RuntimeError(f"分片 {idx} HTTP {r.status_code}")
        body = r.content
        if len(body) != end - start + 1:
            raise RuntimeError(f"分片 {idx} 字节数不符：期望 {end - start + 1} 实收 {len(body)}")
        with open(target, "r+b") as fh:
            fh.seek(start)
            fh.write(body)

    await asyncio.gather(*[_one(i) for i in range(concurrency)])


async def _merge_dash(
    video: Path, audio: Path | None, target: Path, ffmpeg_path: str = "ffmpeg"
) -> DownloadResult:
    """用 ffmpeg -c copy 把 DASH 视频/音频合并成 mp4（照抄 bili-sync multi_fetch_and_merge）。"""
    if audio is None:
        if video != target:
            video.replace(target)
        return DownloadResult(ok=True, video_file=target.name, message="下载完成（无音轨）")
    cmd = [
        ffmpeg_path, "-y",
        "-i", str(video), "-i", str(audio),
        "-c", "copy", "-strict", "unofficial",
        "-f", "mp4", str(target),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        return DownloadResult(ok=False, message=f"ffmpeg 合并失败: {stderr.decode(errors='ignore')[-300:]}")
    return DownloadResult(ok=True, video_file=target.name, message="下载完成")


async def download_dash(
    video_urls: list[str],
    audio_urls: list[str],
    out_dir: "Path | str",
    name: str,
    cookie: str = "",
    *,
    merge: bool = True,
) -> DownloadResult:
    """B站 DASH 下载：视频流 + 音频流分别取最优地址，合并成 mp4。

    - 每条流的多 CDN 备份地址依次轮换（主地址失败不放弃）。
    - 大文件走 Range 分片并行。
    - 用 ffmpeg -c copy 合并（不重编码，秒级完成）。
    """
    import httpx

    out_dir = _as_dir(out_dir)
    if out_dir is None:
        return DownloadResult(ok=False, message="未指定下载目录（存储根未配置）")
    if not video_urls:
        return DownloadResult(ok=False, message="没有可用的视频地址")
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = _bili_headers(cookie)
    safe = _safe_name(name)
    video_tmp = out_dir / f".{safe}.video.m4s"
    audio_tmp = out_dir / f".{safe}.audio.m4s"
    target = out_dir / f"{safe}.mp4"

    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        # 已经是混合流（durl/flv）或不需要合并：直接落 target
        if not audio_urls or not merge:
            res = await _fetch_to_file(client, video_urls, target, headers)
            return res
        vres = await _fetch_to_file(client, video_urls, video_tmp, headers)
        if not vres.ok:
            return DownloadResult(ok=False, message=f"视频流下载失败：{vres.message}")
        ares = await _fetch_to_file(client, audio_urls, audio_tmp, headers)
        if not ares.ok:
            video_tmp.unlink(missing_ok=True)
            return DownloadResult(ok=False, message=f"音频流下载失败：{ares.message}")

    result = await _merge_dash(video_tmp, audio_tmp, target)
    video_tmp.unlink(missing_ok=True)
    audio_tmp.unlink(missing_ok=True)
    return result


def _safe_name(name: str) -> str:
    """把标题规整成安全文件名（照抄抖小云 SanitizeLinuxFileName 的关键约束）。

    - 去掉路径分隔符与控制字符，替换各平台非法字符；
    - 按 UTF-8 **字节**截断到 100 字节（Linux 255 字节上限内的安全值），
      且不截断多字节字符中间；
    - 去掉空格（抖小云同样 .Replace(" ", "")）。
    """
    import re

    text = str(name or "").strip()
    for ch in ["/", "\\", "\0", "\n", "\r", "\v", "\f", ":", "*", "?", '"', "<", ">", "|"]:
        text = text.replace(ch, "_")
    text = text.replace(" ", "")
    raw = text.encode("utf-8")
    if len(raw) > 100:
        text = raw[:100].decode("utf-8", errors="ignore")
    text = re.sub(r"[_]{2,}", "_", text).strip("._")
    return text or "video"
