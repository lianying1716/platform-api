"""能力探测：文本 JSON、图片真看、视频真看、ffmpeg 跳过与手工覆盖。"""

import asyncio
import json

import pytest

from app.models import AiModel, AiProvider
from app.services import model_probe


def _seed(session):
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai", base_url="https://api.example.com/v1",
        api_key_encrypted="x", config_revision=1,
    )
    session.add(provider)
    session.flush()
    model = AiModel(provider_id=provider.id, model_key="m", display_name="m", capabilities={})
    session.add(model)
    session.commit()
    return provider, model


def test_probe_text_image_video_success(monkeypatch, session, tmp_path):
    provider, model = _seed(session)
    monkeypatch.setattr(model_probe, "_decrypt_key", lambda _p: "k")
    monkeypatch.setattr(model_probe, "_red_image", lambda _color=None: b"PNG")
    monkeypatch.setattr(model_probe, "_choose_probe_color", lambda: "red")
    monkeypatch.setattr(model_probe, "_red_blue_video", lambda *_a, **_k: b"MP4")
    # 视频探测以 shutil.which("ffmpeg") 为门：无 ffmpeg 的机器上会走"跳过"分支而非探测。
    # 这里断言的是"探测成功"路径，故强制 ffmpeg 可用，避免依赖 CI 是否装了 ffmpeg。
    monkeypatch.setattr(model_probe.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    async def fake_chat(*args, **kwargs):
        body = kwargs["request"]
        text = json.dumps(body, ensure_ascii=False)
        if "color" in text.lower():
            return {"text": '{"has_image": true, "color": "red"}', "usage": {}, "model": "m"}
        return {"text": '{"ok": true, "echo": "workbench"}', "usage": {}, "model": "m"}

    monkeypatch.setattr(model_probe, "_chat", fake_chat)

    async def fake_video_chat(*args, **kwargs):
        return {"text": '{"first": "red", "second": "blue"}', "usage": {}, "model": "m"}

    monkeypatch.setattr(model_probe, "_video_chat", fake_video_chat)
    out = asyncio.run(model_probe.probe_model(session, model.id))
    caps = out.capabilities
    assert caps["text"]["value"] is True
    assert caps["json"]["value"] is True
    assert caps["image"]["value"] is True
    assert caps["video"]["value"] is True
    assert out.probe_status == "passed"


def test_probe_video_skipped_without_ffmpeg(monkeypatch, session):
    _provider, model = _seed(session)
    monkeypatch.setattr(model_probe, "_decrypt_key", lambda _p: "k")
    monkeypatch.setattr(model_probe.shutil, "which", lambda _name: None)

    async def fake_chat(*args, **kwargs):
        return {"text": '{"ok": true, "echo": "workbench"}', "usage": {}, "model": "m"}

    monkeypatch.setattr(model_probe, "_chat", fake_chat)
    out = asyncio.run(model_probe.probe_model(session, model.id, image=False, video=True))
    assert out.capabilities["video"]["value"] is False
    assert "ffmpeg" in out.capabilities["video"]["detail"]
    assert out.probe_status in ("partial", "failed")


def test_manual_video_capability(session):
    _provider, model = _seed(session)
    asyncio.run(
        model_probe.set_manual_video_capability(
            session, model.id, value=True, transport="openai_video_url", reason="自有网关实测"
        )
    )
    session.refresh(model)
    video = model.capabilities["video"]
    assert video["value"] is True
    assert video["source"] == "manual_override"
    assert video["transport"] == "openai_video_url"
    assert video["auth_style"] == "bearer"
    assert video["reason"] == "自有网关实测"
