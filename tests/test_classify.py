"""判类 Pass 0 测试（纯文本分类；注入 _post_json，不联网）。

覆盖：正常解析、非法枚举回退 other、字段容错、网络/解析失败抛异常、
封面图（use_cover）开关对 payload 的影响、封面缺失/为空抛异常、提示词含类型目录。
"""

import asyncio
import types

import pytest

import app.modules.classify as c


def _fake(content: str, *, capture: dict | None = None):
    async def fake_post(url, headers, body, timeout):
        if capture is not None:
            capture["url"] = url
            capture["headers"] = headers
            capture["body"] = body
            capture["timeout"] = timeout
        return {"choices": [{"message": {"content": content}}]}

    return fake_post


TAXONOMY = {"commerce": "带货/种草/开箱", "drama": "剧情/段子", "other": "其他"}


def _run(monkeypatch, content, **kwargs):
    capture = kwargs.get("capture")
    cover = kwargs.get("cover_path")

    async def call_model(*, request, parse_result, image_required=False):
        if capture is not None:
            capture["request"] = request
            capture["image_required"] = image_required
        return types.SimpleNamespace(data=parse_result(content))

    return asyncio.run(
        c.classify(
            "标题",
            "作者",
            30,
            "转录",
            TAXONOMY,
            cover_path=cover,
            use_cover=bool(cover),
            call_model=call_model,
        )
    )


def test_classify_parses():
    out = _run(None, '{"type":"commerce","confidence":0.9,"reason":"x","signals":["链接"]}')
    assert out["type"] == "commerce"
    assert out["confidence"] == 0.9
    assert out["reason"] == "x"
    assert out["signals"] == ["链接"]


def test_classify_unknown_enum():
    out = _run(None, '{"type":"nope","confidence":0.5}')
    assert out["type"] == "other"
    assert out["confidence"] == 0
    assert out["reason"]
    assert out["signals"] == []


def test_classify_missing_type_falls_back_other():
    out = _run(None, '{"confidence":0.5,"reason":"无类型"}')
    assert out["type"] == "other"
    assert out["confidence"] == 0


def test_classify_confidence_clamped_and_coerced():
    assert _run(None, '{"type":"commerce","confidence":1.5}')["confidence"] == 1.0
    assert _run(None, '{"type":"commerce","confidence":-3}')["confidence"] == 0.0
    assert _run(None, '{"type":"commerce","confidence":"0.7"}')["confidence"] == 0.7
    assert _run(None, '{"type":"commerce","confidence":"abc"}')["confidence"] == 0.0


def test_classify_signals_normalized():
    assert _run(None, '{"type":"commerce","signals":"链接"}')["signals"] == []
    assert _run(None, '{"type":"commerce","signals":["a",1,null]}')["signals"] == ["a"]


def test_classify_malformed_json_raises():
    with pytest.raises(Exception):
        _run(None, "not json at all")


def test_classify_missing_gateway_is_programming_error():
    with pytest.raises(RuntimeError, match="ModelAccessService"):
        asyncio.run(
            c.classify(
                "t", "a", 10, "tr", TAXONOMY, cover_path=None,
            )
        )


def test_classify_payload_text_only():
    cap: dict = {}
    out = _run(None, '{"type":"commerce","confidence":0.9}', capture=cap)
    assert out["type"] == "commerce"
    body = cap["request"]
    assert "model" not in body  # 由网关填模型，模块不得猜
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] <= 0.3
    content = body["messages"][-1]["content"]
    assert isinstance(content, str)
    assert "image_url" not in content
    sys_prompt = body["messages"][0]["content"]
    assert "commerce" in sys_prompt and "带货/种草/开箱" in sys_prompt
    assert "JSON" in sys_prompt


def test_classify_prompt_includes_metadata():
    cap: dict = {}
    _run(None, '{"type":"commerce","confidence":0.9}', capture=cap)
    user = cap["request"]["messages"][-1]["content"]
    assert "标题" in user and "作者" in user and "30" in user and "转录" in user


def test_classify_cover_injected(tmp_path):
    cap: dict = {}
    img = tmp_path / "cov.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    _run(None, '{"type":"commerce","confidence":0.9}', capture=cap, cover_path=str(img))
    content = cap["request"]["messages"][-1]["content"]
    assert isinstance(content, list)
    img_part = next(p for p in content if p["type"] == "image_url")
    assert img_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert cap["image_required"] is True


def test_classify_cover_missing_raises_before_gateway():
    called = False

    async def call_model(**_kwargs):
        nonlocal called
        called = True

    missing = "/nonexistent/definitely-not-here/cover.jpg"
    with pytest.raises(FileNotFoundError) as ei:
        asyncio.run(
            c.classify(
                "t", "a", 10, "tr", TAXONOMY, cover_path=missing,
                use_cover=True, call_model=call_model,
            )
        )
    assert missing in str(ei.value)
    assert called is False


def test_build_messages_pure():
    sys_prompt, user = c.build_messages("t", "a", 10, "tr", TAXONOMY)
    assert isinstance(sys_prompt, str) and "commerce" in sys_prompt
    assert "t" in user and "tr" in user
