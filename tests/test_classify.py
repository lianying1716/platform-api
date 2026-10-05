"""判类 Pass 0 测试（纯文本分类；注入 _post_json，不联网）。

覆盖：正常解析、非法枚举回退 other、字段容错、网络/解析失败抛异常、
封面图（use_cover）开关对 payload 的影响、封面缺失/为空抛异常、提示词含类型目录。
"""

import asyncio

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
    monkeypatch.setattr(c, "_post_json", _fake(content, **kwargs))
    return asyncio.run(
        c.classify(
            "标题",
            "作者",
            30,
            "转录",
            TAXONOMY,
            cover_path=None,
            base_url="http://x/",
            api_key="k",
            model="m",
        )
    )


def test_classify_parses(monkeypatch):
    out = _run(
        monkeypatch,
        '{"type":"commerce","confidence":0.9,"reason":"x","signals":["链接"]}',
    )
    assert out["type"] == "commerce"
    assert out["confidence"] == 0.9
    assert out["reason"] == "x"
    assert out["signals"] == ["链接"]


def test_classify_unknown_enum(monkeypatch):
    out = _run(monkeypatch, '{"type":"nope","confidence":0.5}')
    assert out["type"] == "other"
    assert out["confidence"] == 0
    assert out["reason"]
    assert out["signals"] == []


def test_classify_missing_type_falls_back_other(monkeypatch):
    out = _run(monkeypatch, '{"confidence":0.5,"reason":"无类型"}')
    assert out["type"] == "other"
    assert out["confidence"] == 0


def test_classify_confidence_clamped_and_coerced(monkeypatch):
    # 超出 [0,1] 截断；字符串数字转 float；非法值归 0
    assert _run(monkeypatch, '{"type":"commerce","confidence":1.5}')["confidence"] == 1.0
    assert _run(monkeypatch, '{"type":"commerce","confidence":-3}')["confidence"] == 0.0
    assert _run(monkeypatch, '{"type":"commerce","confidence":"0.7"}')["confidence"] == 0.7
    assert _run(monkeypatch, '{"type":"commerce","confidence":"abc"}')["confidence"] == 0.0


def test_classify_signals_normalized(monkeypatch):
    # signals 非列表 -> 空；列表内非字符串项丢弃
    assert _run(monkeypatch, '{"type":"commerce","signals":"链接"}')["signals"] == []
    assert _run(monkeypatch, '{"type":"commerce","signals":["a",1,null]}')["signals"] == ["a"]


def test_classify_malformed_json_raises(monkeypatch):
    with pytest.raises(Exception):
        _run(monkeypatch, "not json at all")


def test_classify_empty_choices_raises(monkeypatch):
    async def fake_post(url, headers, body, timeout):
        return {"choices": []}

    monkeypatch.setattr(c, "_post_json", fake_post)
    with pytest.raises(Exception):
        asyncio.run(
            c.classify("t", "a", 10, "tr", TAXONOMY, cover_path=None,
                       base_url="http://x", api_key="k", model="m")
        )


def test_classify_network_error_propagates(monkeypatch):
    async def boom(url, headers, body, timeout):
        raise RuntimeError("网络挂了")

    monkeypatch.setattr(c, "_post_json", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(
            c.classify("t", "a", 10, "tr", TAXONOMY, cover_path=None,
                       base_url="http://x", api_key="k", model="m")
        )


def test_classify_payload_text_only(monkeypatch):
    cap: dict = {}
    out = _run(monkeypatch, '{"type":"commerce","confidence":0.9}', capture=cap)
    assert out["type"] == "commerce"
    # base 去尾斜杠拼 /chat/completions；Bearer 鉴权
    assert cap["url"] == "http://x/chat/completions"
    assert cap["headers"]["Authorization"] == "Bearer k"
    body = cap["body"]
    assert body["model"] == "m"
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] <= 0.3
    # 无封面 -> content 是纯字符串，且不含 image
    content = body["messages"][-1]["content"]
    assert isinstance(content, str)
    assert "image_url" not in content
    # 系统提示词含类型目录（枚举 + 中文）与「只输出 JSON」约束
    sys_prompt = body["messages"][0]["content"]
    assert "commerce" in sys_prompt and "带货/种草/开箱" in sys_prompt
    assert "JSON" in sys_prompt
    assert "媒体端" not in sys_prompt  # 防止误用通用模板占位


def test_classify_prompt_includes_metadata(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(c, "_post_json", _fake('{"type":"commerce","confidence":0.9}', capture=cap))
    asyncio.run(
        c.classify("我的标题", "某作者", 42, "这是转录内容",
                   TAXONOMY, cover_path=None, base_url="http://x", api_key="k", model="m")
    )
    user = cap["body"]["messages"][-1]["content"]
    assert isinstance(user, str)
    assert "我的标题" in user
    assert "某作者" in user
    assert "42" in user
    assert "这是转录内容" in user


def test_classify_cover_injected(monkeypatch, tmp_path):
    cap: dict = {}
    monkeypatch.setattr(c, "_post_json", _fake('{"type":"commerce","confidence":0.9}', capture=cap))
    img = tmp_path / "cov.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")  # 最小 JPEG 字节
    asyncio.run(
        c.classify("t", "a", 10, "tr", TAXONOMY, cover_path=str(img),
                   base_url="http://x", api_key="k", model="m", use_cover=True)
    )
    content = cap["body"]["messages"][-1]["content"]
    assert isinstance(content, list)
    kinds = [part["type"] for part in content]
    assert "image_url" in kinds
    img_part = next(p for p in content if p["type"] == "image_url")
    assert img_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_classify_cover_not_injected_by_default(monkeypatch, tmp_path):
    cap: dict = {}
    monkeypatch.setattr(c, "_post_json", _fake('{"type":"commerce","confidence":0.9}', capture=cap))
    img = tmp_path / "cov.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    asyncio.run(
        c.classify("t", "a", 10, "tr", TAXONOMY, cover_path=str(img),
                   base_url="http://x", api_key="k", model="m")  # use_cover 默认 False
    )
    content = cap["body"]["messages"][-1]["content"]
    assert isinstance(content, str)


def test_classify_cover_missing_raises(monkeypatch):
    # 用户显式要求用封面（use_cover=True）但文件不存在 -> 抛 FileNotFoundError，不静默降级纯文本。
    called = False

    async def fake_post(url, headers, body, timeout):
        nonlocal called
        called = True
        return {"choices": [{"message": {"content": '{"type":"commerce"}'}}]}

    monkeypatch.setattr(c, "_post_json", fake_post)
    missing = "/nonexistent/definitely-not-here/cover.jpg"
    with pytest.raises(FileNotFoundError) as ei:
        asyncio.run(
            c.classify("t", "a", 10, "tr", TAXONOMY, cover_path=missing,
                       base_url="http://x", api_key="k", model="m", use_cover=True)
        )
    assert missing in str(ei.value)
    assert called is False  # 封面读取失败时不应发起 LLM 调用


def test_classify_cover_empty_raises(monkeypatch, tmp_path):
    # 空封面文件无法作为图片输入 -> ValueError，不静默降级纯文本。
    async def fake_post(url, headers, body, timeout):
        raise AssertionError("封面读取失败时不应发起 LLM 调用")

    monkeypatch.setattr(c, "_post_json", fake_post)
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    with pytest.raises(ValueError):
        asyncio.run(
            c.classify("t", "a", 10, "tr", TAXONOMY, cover_path=str(empty),
                       base_url="http://x", api_key="k", model="m", use_cover=True)
        )


def test_classify_custom_timeout(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(c, "_post_json", _fake('{"type":"commerce"}', capture=cap))
    asyncio.run(
        c.classify("t", "a", 10, "tr", TAXONOMY, cover_path=None,
                   base_url="http://x", api_key="k", model="m", timeout=12)
    )
    assert cap["timeout"] == 12


def test_build_messages_pure():
    # build_messages 可用作纯函数拼装（服务层复用）
    sys_prompt, user = c.build_messages("t", "a", 10, "tr", TAXONOMY)
    assert isinstance(sys_prompt, str) and "commerce" in sys_prompt
    assert "t" in user and "tr" in user
