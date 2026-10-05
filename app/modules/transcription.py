"""faster-whisper 转录封装。

红线：faster-whisper 不直接暴露为 API，只能经队列 + worker 调用（本模块由
workers/handlers.py 的 handle_transcribe 调用）。模型按需懒加载并进程内缓存，
避免每个任务重复加载权重。

依赖：faster-whisper（pyproject 主依赖）+ ffmpeg（PATH）。CPU 默认 int8 量化。
"""

import asyncio
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger("platform.transcription")

_model = None  # 进程内缓存的 WhisperModel
_model_key: tuple | None = None


def _resolve_device(device: str) -> tuple[str, str]:
    """auto 时探测有无 CUDA，返回 (device, compute_type)。"""
    if device != "auto":
        return device, settings.whisper_compute_type
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _load_model(model_name: str, device: str):
    global _model, _model_key
    dev, compute = _resolve_device(device)
    key = (model_name, dev, compute)
    if _model is not None and _model_key == key:
        return _model
    from faster_whisper import WhisperModel

    logger.info("加载 whisper 模型 %s (device=%s compute=%s)", model_name, dev, compute)
    _model = WhisperModel(model_name, device=dev, compute_type=compute)
    _model_key = key
    return _model


def _transcribe_sync(media_path: str, options: dict) -> dict:
    if not Path(media_path).exists():
        raise FileNotFoundError(f"待转录文件不存在: {media_path}")
    model_name = options.get("model") or settings.whisper_model
    device = options.get("device") or settings.whisper_device
    model = _load_model(model_name, device)
    segments, info = model.transcribe(
        media_path,
        language=options.get("language"),
        word_timestamps=options.get("word_timestamps", False),
        vad_filter=options.get("vad_filter", True),
    )
    seg_list = []
    texts = []
    for seg in segments:
        seg_list.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()})
        texts.append(seg.text.strip())
    return {
        "language": info.language,
        "model": model_name,
        "segments": seg_list,
        "full_text": "\n".join(texts),
    }


async def transcribe(media_path: str, options: dict | None = None) -> dict:
    """异步入口：把阻塞的转录丢到线程池，避免卡住 worker 事件循环。"""
    return await asyncio.to_thread(_transcribe_sync, media_path, options or {})
