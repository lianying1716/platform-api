"""B站下载：view+playurl 解析 DASH + 多 CDN 轮换 + ffmpeg 合并（看齐上游 bili-sync）。

回归背景（实测）：
- 我们此前 B站没有 download_url，一律落 yt-dlp；而 B站对 yt-dlp 的元数据请求直接
  回 412 风控 —— 连公开视频都下不动。
- bili-sync 的正确做法：/x/web-interface/view 拿 cid → /x/player/playurl 拿 dash 流，
  取最高画质视频流 + 最高码率音频流，各流有多个 backupUrl 轮换，Range 分片并行，
  最后 ffmpeg -c copy 合并。
- B站 CDN 要求 User-Agent + Referer，缺任一个直接 403（实测）。
"""

import asyncio
from pathlib import Path

import pytest

from app.modules import bilibili, media_download


def _stream(urls, sid=80, bw=2000000):
    return {"id": sid, "bandwidth": bw, "baseUrl": urls[0], "backupUrl": urls[1:]}


def test_pick_best_video_prefers_highest_quality_then_bandwidth():
    streams = [
        {"id": 32, "bandwidth": 999999},
        {"id": 80, "bandwidth": 100},
        {"id": 80, "bandwidth": 5000000},
    ]
    best = bilibili._pick_best_video_stream(streams)
    assert best["id"] == 80 and best["bandwidth"] == 5000000, "清晰度优先，同档取高码率"


def test_stream_urls_includes_all_backups_and_dedups():
    urls = bilibili._stream_urls(_stream(["a", "b", "a", "c"]))
    assert urls == ["a", "b", "c"]


def test_audio_urls_from_dash():
    """音频按 id（音质档位）优先，同档取高码率。

    B站 id 越大音质越高：30216=64k < 30232=132k < 30280=192k(Hi-Res)，
    所以 30280 应当胜过 bandwidth 更大的低档。
    """
    streams = [{"id": 30216, "bandwidth": 999999}, {"id": 30280, "bandwidth": 1}]
    assert bilibili._pick_best_audio_stream(streams)["id"] == 30280
    same_tier = [{"id": 30280, "bandwidth": 1}, {"id": 30280, "bandwidth": 500}]
    assert bilibili._pick_best_audio_stream(same_tier)["bandwidth"] == 500


# ------------------------------------------------------------------ download_dash

@pytest.mark.asyncio
async def test_download_dash_rotates_to_backup_url(tmp_path, monkeypatch):
    """主 CDN 失败必须轮换 backupUrl（照抄 bili-sync multi_fetch_internal）。"""
    calls = []

    async def fake_probe(client, url, headers, is_stream):
        calls.append(("probe", url))
        return 0

    async def fake_serial(client, url, target, headers):
        calls.append(("get", url))
        if "primary" in url:
            raise RuntimeError("HTTP 403")
        Path(target).write_bytes(b"x" * 1024)

    monkeypatch.setattr(media_download, "_probe_size", fake_probe)
    monkeypatch.setattr(media_download, "_fetch_serial", fake_serial)

    async def fake_merge(video, audio, target, ffmpeg_path="ffmpeg"):
        Path(target).write_bytes(b"merged")
        return media_download.DownloadResult(ok=True, video_file=Path(target).name)

    monkeypatch.setattr(media_download, "_merge_dash", fake_merge)

    res = await media_download.download_dash(
        ["https://primary/x", "https://backup/x"], ["https://a/x"], tmp_path, "n", cookie="SESSDATA=1"
    )
    assert res.ok
    assert ("get", "https://backup/x") in calls, "主地址失败后应轮换备份"


@pytest.mark.asyncio
async def test_download_dash_reports_all_urls_failed(tmp_path, monkeypatch):
    async def fake_probe(client, url, headers, is_stream):
        return 0

    async def fake_serial(client, url, target, headers):
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(media_download, "_probe_size", fake_probe)
    monkeypatch.setattr(media_download, "_fetch_serial", fake_serial)

    res = await media_download.download_dash(
        ["https://a/x", "https://b/x"], ["https://c/x"], tmp_path, "n"
    )
    assert not res.ok
    assert "均失败" in res.message


@pytest.mark.asyncio
async def test_download_dash_rejects_empty_video_urls(tmp_path):
    res = await media_download.download_dash([], ["https://a/x"], tmp_path, "n")
    assert not res.ok and "视频地址" in res.message


def test_bili_headers_include_ua_and_referer():
    """B站 CDN 缺 UA 或 Referer 直接 403（实测）。"""
    h = media_download._bili_headers("SESSDATA=abc")
    assert "bilibili.com" in h["Referer"]
    assert "Mozilla" in h["User-Agent"]
    assert h["Cookie"] == "SESSDATA=abc"
    assert "Cookie" not in media_download._bili_headers("")


def test_safe_name_truncates_and_strips_illegal():
    name = media_download._safe_name("a/b:c*d?e\"f<g>h|i" + "长" * 200)
    assert "/" not in name and ":" not in name and '"' not in name
    assert len(name.encode("utf-8")) <= 100
    assert media_download._safe_name("") == "video"
    assert media_download._safe_name("  ") == "video"
    # 不截断多字节字符中间
    assert "�" not in media_download._safe_name("中" * 100)


@pytest.mark.asyncio
async def test_fetch_serial_times_out_on_stall(tmp_path, monkeypatch):
    """CDN 挂着不返回数据时必须按空闲超时失败（照抄抖小云 streamTimeout）。

    否则一条卡住的流会让 worker 槽永久占用。
    """
    monkeypatch.setattr(media_download, "_STREAM_STALL_SECONDS", 0.3)

    class _Resp:
        status_code = 200
        headers = {"content-length": "100"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def aiter_bytes(self, n):
            async def _gen():
                await asyncio.sleep(5)  # 远超空闲超时
                yield b"x"
            return _gen()

    class _Client:
        def stream(self, method, url, headers=None):
            return _Resp()

    import asyncio as _asyncio
    with pytest.raises(RuntimeError, match="超时"):
        await _asyncio.wait_for(
            media_download._fetch_serial(_Client(), "https://x/y", tmp_path / "f.bin", {}),
            timeout=3,
        )


@pytest.mark.asyncio
async def test_fetch_serial_ok_when_complete(tmp_path):
    """正常流：字节数与 Content-Length 相符时成功。"""
    payload = b"y" * 4096

    class _Resp:
        status_code = 200
        headers = {"content-length": str(len(payload))}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def aiter_bytes(self, n):
            async def _gen():
                yield payload
            return _gen()

    class _Client:
        def stream(self, method, url, headers=None):
            return _Resp()

    target = tmp_path / "ok.bin"
    await media_download._fetch_serial(_Client(), "https://x/y", target, {})
    assert target.read_bytes() == payload
