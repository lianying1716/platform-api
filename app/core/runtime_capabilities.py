"""Runtime capability checks shared by diagnostics, deployment smoke and UI.

These checks describe what the running process can actually do.  They are
deliberately independent of any one deployment: a local install, a container and
a hosted instance must all get the same answer for the same environment.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RuntimeCapability:
    key: str
    label: str
    available: bool
    required: bool
    detail: str
    fix: str

    def as_dict(self) -> dict:
        return asdict(self)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def runtime_capabilities() -> list[RuntimeCapability]:
    ffmpeg = shutil.which("ffmpeg")
    faster_whisper = _module_available("faster_whisper")
    return [
        RuntimeCapability(
            key="transcription",
            label="语音转录",
            available=bool(ffmpeg and faster_whisper),
            required=True,
            detail=(
                "faster-whisper 与 FFmpeg 就绪"
                if ffmpeg and faster_whisper
                else "缺少 " + "、".join(
                    item
                    for item, ok in (("faster-whisper", faster_whisper), ("FFmpeg", bool(ffmpeg)))
                    if not ok
                )
            ),
            fix="安装 transcribe 依赖并确保 ffmpeg 在 PATH；容器镜像需包含转录 extra",
        ),
    ]
