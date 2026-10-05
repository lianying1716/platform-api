"""L2 抽帧模块测试（依赖本机 ffmpeg；缺失则 skip）。"""
import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 未安装")


def _make_video(path, duration: int = 3) -> None:
    """用 lavfi 生成测试视频（testsrc 有明显画面变化，便于场景切分）。"""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=160x120:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def test_extract_frames(tmp_path):
    from app.modules.frames import extract

    vid = tmp_path / "v.mp4"
    _make_video(vid)
    out = extract(str(vid), str(tmp_path / "frames"), max_frames=6, per_min=10)
    assert out["count"] >= 1
    assert all(os.path.exists(f["path"]) for f in out["frames"])
    assert all(f["path"].endswith(".jpg") for f in out["frames"])
    # 帧元数据字段完整且时间戳有序
    ts = [f["timestamp_sec"] for f in out["frames"]]
    assert ts == sorted(ts)
    assert {"scene_index", "timestamp_sec", "path", "is_keyframe"} <= set(out["frames"][0])


def test_extract_respects_max_frames(tmp_path):
    """帧数不超过上限，且全部落盘。"""
    from app.modules.frames import extract

    vid = tmp_path / "v.mp4"
    _make_video(vid, duration=4)
    out = extract(str(vid), str(tmp_path / "frames"), max_frames=3, per_min=30)
    assert out["count"] <= 3
    assert len(out["frames"]) == out["count"]
    assert all(os.path.exists(f["path"]) for f in out["frames"])


def test_extract_missing_video_raises(tmp_path):
    from app.modules.frames import extract

    with pytest.raises(FileNotFoundError):
        extract(str(tmp_path / "nope.mp4"), str(tmp_path / "out"), max_frames=6, per_min=10)
