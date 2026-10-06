"""L2 抽帧：ffmpeg 场景切分 + 关键帧抽取（零模型成本）。

流程：
1. `ffprobe` 取视频时长；
2. `ffmpeg` 用 `select='gt(scene,THRESHOLD)'` 做场景切分，解析 `showinfo` 的
   `pts_time` 得到场景切换时间点；
3. 场景帧不足时，按 `per_min`（每分钟帧数）时间间隔均匀补帧：从均匀候选点中
    等距抽样到剩余预算，保证补帧覆盖整段时长（而非集中于前半段）；
4. 合并去重、按上限 `max_frames` 截断，逐帧导出 JPEG（`-q:v 3`，`frame_%03d.jpg`）。

依赖：ffmpeg / ffprobe（PATH），不引入新的 Python 依赖。保持纯同步实现，
由 worker 的 `handle_extract_frames` 在线程池中调用（见 plan Task 7）。
"""

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("platform.frames")

# 解析 showinfo 输出的时间戳：`... pts_time:1.234 ...`
_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
# 补帧去重邻域系数：与已选帧间隔小于 该系数 × 均匀步长 时视为重复
_DEDUP_FACTOR = 0.5


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """执行外部命令（禁用 shell），返回 CompletedProcess；不抛错由调用方判定。"""
    return subprocess.run(cmd, capture_output=True, text=True)


def _probe_duration(video_path: str) -> float:
    """用 ffprobe 取视频时长（秒）；取不到时返回 0.0（退化为只做场景切分）。"""
    proc = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
    )
    if proc.returncode != 0:
        return 0.0
    try:
        return float(proc.stdout.strip())
    except (TypeError, ValueError):
        return 0.0


def _scene_timestamps(video_path: str, scene_threshold: float) -> list[float]:
    """场景切分，返回按出现顺序的时间戳（秒）。ffmpeg 失败则抛 RuntimeError。

    用 `-f null -` 丢弃画面、只借 `showinfo` 输出时间戳，避免无谓的 JPEG 编码 I/O。
    """
    proc = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            video_path,
            "-vf",
            f"select='gt(scene,{scene_threshold})',showinfo",
            "-fps_mode",
            "vfr",
            "-f",
            "null",
            "-",
        ]
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-5:]
        raise RuntimeError("ffmpeg 场景切分失败: " + " | ".join(tail))
    return [round(float(m), 3) for m in _PTS_RE.findall(proc.stderr or "")]


def _uniform_timestamps(duration: float, per_min: float) -> list[float]:
    """按每分钟 per_min 帧的间隔均匀铺时间点（含 0s，严格小于时长）。

    末尾用严格 `< duration`：落在时长边界上的时间点取不到画面（seek 越界）。
    """
    if duration <= 0 or per_min <= 0:
        return [0.0] if duration <= 0 else []
    interval = 60.0 / per_min
    n = int(duration / interval)
    return [round(i * interval, 3) for i in range(n + 1) if i * interval < duration]


def _uniform_pick(candidates: list[float], k: int) -> list[float]:
    """从候选时间点中等距抽取 k 个（含首尾），去重且升序。

    用于「剩余预算不足铺满全部候选点」时按时间等距抽样，避免截头导致
    只覆盖视频前半段。k 不小于候选数时返回全部候选。
    """
    uniq = sorted(set(candidates))
    if k <= 0 or not uniq:
        return []
    if k >= len(uniq):
        return uniq
    if k == 1:
        return [uniq[len(uniq) // 2]]
    step = (len(uniq) - 1) / (k - 1)
    picked = [uniq[round(i * step)] for i in range(k)]
    return sorted(set(picked))


def _merge_timestamps(
    scene_ts: list[float], duration: float, max_frames: int, per_min: float
) -> list[tuple[float, bool]]:
    """合并场景帧与均匀补帧，去重并按上限截断。

    返回 `[(timestamp_sec, is_keyframe), ...]`，按时间升序。场景帧优先保留：
    场景数超过上限时按等距抽样，保证 is_keyframe 均为 True。
    """
    if max_frames <= 0:
        return []
    # 场景时间点也需严格落在时长内，避免越界取不到画面
    scene_sorted = sorted(t for t in set(scene_ts) if duration <= 0 or t < duration)
    interval = 60.0 / per_min if per_min > 0 else 0.0
    tol = interval * _DEDUP_FACTOR if interval > 0 else 0.0

    # 场景帧数量足够：等距抽样到上限，全部标记为关键帧
    if len(scene_sorted) >= max_frames:
        if max_frames <= 1:
            picked = scene_sorted[:1]
        else:
            step = (len(scene_sorted) - 1) / (max_frames - 1)
            picked = [scene_sorted[round(i * step)] for i in range(max_frames)]
        return [(t, True) for t in sorted(set(picked))]

    merged: list[tuple[float, bool]] = [(t, True) for t in scene_sorted]
    # 补帧：先剔除与已选帧时间过近的均匀候选点，再按剩余预算等距抽样，
    # 使补帧覆盖整段时长而非在前半段耗尽预算（截头会漏掉尾部）。
    remaining = max_frames - len(merged)
    if remaining > 0:
        candidates = [
            t
            for t in _uniform_timestamps(duration, per_min)
            if not any(abs(t - existing) <= tol for existing, _ in merged)
        ]
        merged.extend((t, False) for t in _uniform_pick(candidates, remaining))
    return sorted(merged, key=lambda item: item[0])[:max_frames]


def extract(
    video_path: str,
    out_dir: str,
    *,
    max_frames: int,
    per_min: float,
    scene_threshold: float = 0.3,
) -> dict:
    """抽取关键帧 + 均匀补帧，返回帧元数据。

    Args:
        video_path: 源视频路径。
        out_dir: 输出目录（不存在则创建）。
        max_frames: 返回帧数上限。
        per_min: 补帧密度（帧/分钟）。
        scene_threshold: 场景切换阈值（越大越少）。

    Returns:
        `{"frames": [{"scene_index","timestamp_sec","path","is_keyframe"}], "count": int}`

    Raises:
        FileNotFoundError: 源视频不存在。
        RuntimeError: ffmpeg/ffprobe 执行失败（含 stderr 摘要）。
    """
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(f"待抽帧视频不存在: {video_path}")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 清掉上一次抽帧的残留，避免更少帧的重跑（或 Task 7 按 scene_index upsert）留下孤儿文件
    for stale in out.glob("frame_*.jpg"):
        try:
            stale.unlink()
        except OSError:
            pass

    duration = _probe_duration(video_path)
    scene_ts = _scene_timestamps(video_path, scene_threshold)
    selected = _merge_timestamps(scene_ts, duration, max_frames, per_min)

    frames: list[dict] = []
    for scene_index, (ts, is_keyframe) in enumerate(selected):
        dst = out / f"frame_{scene_index + 1:03d}.jpg"
        proc = _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-y",
                "-ss",
                f"{ts:.3f}",
                "-i",
                video_path,
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(dst),
            ]
        )
        if proc.returncode != 0 or not dst.exists():
            tail = (proc.stderr or "").strip().splitlines()[-5:]
            raise RuntimeError(f"ffmpeg 抽帧失败 @ {ts}s: " + " | ".join(tail))
        frames.append(
            {
                "scene_index": scene_index,
                "timestamp_sec": ts,
                "path": str(dst),
                "is_keyframe": is_keyframe,
            }
        )

    logger.info(
        "抽帧完成 video=%s duration=%.1fs scene=%d final=%d",
        video_path,
        duration,
        len(scene_ts),
        len(frames),
    )
    return {"frames": frames, "count": len(frames)}
