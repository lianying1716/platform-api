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


def _seed_model(session, *, provider_protocol="openai", model_protocol=None, source="inherit"):
    provider = AiProvider(
        name="p", preset_key="custom", protocol=provider_protocol,
        base_url="https://api.example.com/v1", api_key_encrypted="x", config_revision=1,
    )
    session.add(provider)
    session.flush()
    model = AiModel(
        provider_id=provider.id, model_key="m", display_name="m", capabilities={},
        protocol=model_protocol, protocol_source=source,
    )
    session.add(model)
    session.commit()
    return provider, model


def test_resolve_protocol_override_and_inherit(session):
    from app.modules import model_protocols

    provider, model = _seed_model(session, provider_protocol="openai")
    # 未设置覆盖 -> 继承供应商
    assert model_protocols.resolve_protocol(model, provider) == "openai"
    model.protocol = "gemini"
    assert model_protocols.resolve_protocol(model, provider) == "gemini"


def test_probe_auto_detects_protocol_when_inherited_fails(monkeypatch, session):
    """供应商声明 openai，但模型只在 Gemini 原生下能用：探测应自动落到 gemini。"""
    provider, model = _seed_model(session, provider_protocol="openai")
    monkeypatch.setattr(model_probe, "_decrypt_key", lambda _p: "k")
    monkeypatch.setattr(model_probe.shutil, "which", lambda _name: None)  # 跳过视频

    tried: list[str] = []

    async def fake_probe_with(prov, mdl, key, prompt, timeout, *, protocol):
        tried.append(protocol)
        if protocol == "gemini":
            return {"ok": True, "echo": "workbench"}, "json_mode"
        raise RuntimeError("bad_request: 端点在 openai 下不可用")

    monkeypatch.setattr(model_probe, "_probe_json_mode_with", fake_probe_with)

    out = asyncio.run(model_probe.probe_model(session, model.id, image=False, video=False))
    assert out.protocol == "gemini"
    assert out.protocol_source == "auto"
    # 候选顺序：先试继承协议，再按固定顺序试其余协议（anthropic 在 gemini 之前）。
    assert tried == ["openai", "anthropic", "gemini"]
    assert out.capabilities["text"]["value"] is True
    assert out.capabilities["text"]["protocol"] == "gemini"


def test_probe_keeps_inherit_when_inherited_works(monkeypatch, session):
    """继承协议可用时不 pin：保持 inherit，跟随供应商后续变化。"""
    provider, model = _seed_model(session, provider_protocol="openai")
    monkeypatch.setattr(model_probe, "_decrypt_key", lambda _p: "k")
    monkeypatch.setattr(model_probe.shutil, "which", lambda _name: None)

    tried: list[str] = []

    async def fake_probe_with(prov, mdl, key, prompt, timeout, *, protocol):
        tried.append(protocol)
        return {"ok": True, "echo": "workbench"}, "json_mode"

    monkeypatch.setattr(model_probe, "_probe_json_mode_with", fake_probe_with)

    out = asyncio.run(model_probe.probe_model(session, model.id, image=False, video=False))
    assert out.protocol is None
    assert out.protocol_source == "inherit"
    assert tried == ["openai"]  # 第一个就成，不再试其他


def test_probe_manual_protocol_not_overridden(monkeypatch, session):
    """用户手动指定的协议不被自动探测覆盖，也不试其他协议。"""
    provider, model = _seed_model(
        session, provider_protocol="openai", model_protocol="gemini", source="manual"
    )
    monkeypatch.setattr(model_probe, "_decrypt_key", lambda _p: "k")
    monkeypatch.setattr(model_probe.shutil, "which", lambda _name: None)

    tried: list[str] = []

    async def fake_probe_with(prov, mdl, key, prompt, timeout, *, protocol):
        tried.append(protocol)
        return {"ok": True, "echo": "workbench"}, "json_mode"

    monkeypatch.setattr(model_probe, "_probe_json_mode_with", fake_probe_with)

    out = asyncio.run(model_probe.probe_model(session, model.id, image=False, video=False))
    assert out.protocol == "gemini"
    assert out.protocol_source == "manual"
    assert tried == ["gemini"]


def test_probe_falls_back_to_inherit_when_all_protocols_fail(monkeypatch, session):
    provider, model = _seed_model(session, provider_protocol="openai")
    monkeypatch.setattr(model_probe, "_decrypt_key", lambda _p: "k")
    monkeypatch.setattr(model_probe.shutil, "which", lambda _name: None)

    async def fake_probe_with(prov, mdl, key, prompt, timeout, *, protocol):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(model_probe, "_probe_json_mode_with", fake_probe_with)

    out = asyncio.run(model_probe.probe_model(session, model.id, image=False, video=False))
    assert out.protocol is None
    assert out.protocol_source == "inherit"
    assert out.capabilities["text"]["value"] is False
