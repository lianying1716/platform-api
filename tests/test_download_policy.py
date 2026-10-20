"""Download quality must reach both yt-dlp and native platform paths."""

import pytest


def test_ytdlp_format_uses_bounded_height(monkeypatch):
    from pathlib import Path

    from app.modules.media_download import _build_opts

    opts = _build_opts(Path("/tmp/out"), audio_only=False, max_height=720)
    assert "height<=720" in opts["format"]


def test_audio_policy_selects_audio_only():
    from app.domain.download import DownloadPolicy

    policy = DownloadPolicy("audio")
    assert policy.audio_only is True
    assert policy.max_height is None


def test_bilibili_stream_picker_respects_max_height():
    from app.modules.bilibili import _pick_best_video_stream

    streams = [
        {"id": 80, "height": 1080, "bandwidth": 9000},
        {"id": 64, "height": 720, "bandwidth": 5000},
    ]
    assert _pick_best_video_stream(streams, max_height=720)["height"] == 720


def test_douyin_bitrate_picker_respects_max_height():
    from app.modules.douyin import _play_url_list

    aw = {
        "video": {
            "bit_rate": [
                {"height": 1080, "bit_rate": 9000, "play_addr": {"url_list": ["https://cdn/1080"]}},
                {"height": 720, "bit_rate": 5000, "play_addr": {"url_list": ["https://cdn/720"]}},
            ]
        }
    }
    urls = _play_url_list(aw, max_height=720)
    assert urls[0] == "https://cdn/720"
