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


def test_uniform_fill_covers_whole_duration():
    """补帧预算有限时须铺满整段时长，而非只覆盖前半段（review Important #1）。"""
    from app.modules.frames import _merge_timestamps

    # 600s 视频、单场景 @300s、per_min=6、max_frames=24：均匀候选点 0..590 共 60 个，
    # 修复后应等距抽样到整段，至少覆盖到尾段。
    merged = _merge_timestamps([300.0], 600.0, 24, 6)
    ts = sorted(t for t, _ in merged)
    assert len(ts) == 24
    assert max(ts) > 400.0, f"尾部无帧，覆盖仅到 {max(ts)}s"
    assert ts[-1] >= 500.0


def test_merge_max_frames_non_positive_returns_empty():
    """max_frames<=0 时不得返回帧（review Minor #3）。"""
    from app.modules.frames import _merge_timestamps

    assert _merge_timestamps([1, 2, 3, 4, 5], 6.0, 0, 60) == []
    assert _merge_timestamps([1, 2, 3, 4, 5], 6.0, -3, 60) == []


def test_hardcut_scene_detection(tmp_path):
    """真实硬切（红/蓝/绿各 2s）走场景检测分支，切点应被识别为关键帧。"""
    from app.modules.frames import extract

    parts = []
    for name, color in (("r", "red"), ("b", "blue"), ("g", "green")):
        p = tmp_path / f"{name}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color={color}:duration=2:size=160x120:rate=10",
             "-pix_fmt", "yuv420p", str(p)],
            capture_output=True, check=True,
        )
        parts.append(p)
    concat = tmp_path / "list.txt"
    concat.write_text("".join(f"file {p.name}\n" for p in parts))
    vid = tmp_path / "hardcut.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(vid)],
        capture_output=True, check=True,
    )

    out = extract(str(vid), str(tmp_path / "frames"), max_frames=6, per_min=10)
    keyframes = [f["timestamp_sec"] for f in out["frames"] if f["is_keyframe"]]
    # 场景切分识别到两个硬切 (~2s, ~4s)
    assert any(abs(t - 2.0) < 0.5 for t in keyframes), keyframes
    assert any(abs(t - 4.0) < 0.5 for t in keyframes), keyframes


def test_rerun_cleans_stale_frames(tmp_path):
    """同目录更少帧重跑后不残留旧 frame_*.jpg（review Minor #2）。"""
    from app.modules.frames import extract

    vid = tmp_path / "v.mp4"
    _make_video(vid, duration=4)
    out_dir = tmp_path / "frames"
    first = extract(str(vid), str(out_dir), max_frames=10, per_min=600)
    assert first["count"] > 2
    second = extract(str(vid), str(out_dir), max_frames=2, per_min=600)
    assert second["count"] == 2
    leftover = sorted(p.name for p in out_dir.glob("frame_*.jpg"))
    assert leftover == ["frame_001.jpg", "frame_002.jpg"], leftover
