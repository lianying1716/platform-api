"""Runtime capability checks shared by diagnostics, deployment smoke and UI.

These checks describe what the running process can actually do.  They are
deliberately independent of any one deployment: a local install, a container and
a hosted instance must all get the same answer for the same environment.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
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


def _transcription_stack_error() -> str:
    """Import the runtime pair in a fresh process and catch ABI/API drift.

    ``find_spec`` only proves that package names exist.  A production image can
    still fail at the first audio decode when ``av`` and ``faster-whisper`` are
    incompatible; running the import in a child keeps optional imports from
    contaminating the API process.
    """
    probe = (
        "import av, faster_whisper; "
        "print(av.__version__, faster_whisper.__version__)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "transcription import failed").strip()[-300:]
    return ""


def runtime_capabilities() -> list[RuntimeCapability]:
    ffmpeg = shutil.which("ffmpeg")
    faster_whisper = _module_available("faster_whisper")
    stack_error = _transcription_stack_error() if ffmpeg and faster_whisper else ""
    transcription_ok = bool(ffmpeg and faster_whisper and not stack_error)
    return [
        RuntimeCapability(
            key="transcription",
            label="语音转录",
            available=transcription_ok,
            required=True,
            detail=(
                "faster-whisper 与 FFmpeg 就绪"
                if transcription_ok
                else f"转录依赖无法导入：{stack_error}"
                if stack_error
                else "缺少 " + "、".join(
                    item
                    for item, ok in (("faster-whisper", faster_whisper), ("FFmpeg", bool(ffmpeg)))
                    if not ok
                )
            ),
            fix="按 uv.lock 安装 transcribe 依赖并确保 ffmpeg 在 PATH；容器镜像必须锁定依赖版本",
        ),
    ]
