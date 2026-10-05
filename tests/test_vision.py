"""视频理解 provider 适配器测试（payload 拼装 / JSON 容错 / 请求注入，不联网）。

覆盖：
- `build_payload` 两种 provider（qwen OpenAI 兼容 `video_url` / gemini 原生 `inlineData`）的
  端点、body 结构与自定义 mime；未知 provider 抛错。
- `parse_structured` 对 Markdown 代码围栏、纯 JSON、夹带文本的容错与失败抛错。
- `understand` 读文件、体积上限、base_url 拼接、鉴权头注入、两种 provider 的响应文本提取。
"""

import asyncio
import base64

import pytest

import app.modules.vision as v


def _fake(data: dict, *, capture: dict | None = None):
    async def fake_post(url, headers, body, timeout):
        if capture is not None:
            capture["url"] = url
            capture["headers"] = headers
            capture["body"] = body
            capture["timeout"] = timeout
        return data

    return fake_post


# --------------------------------------------------------------------------- build_payload


def test_build_payload_qwen():
    path, headers, body = v.build_payload("qwen", "qwen3.8-flash", "AAA", "提示")
    # qwen 走 OpenAI 兼容 chat/completions，视频以 video_url data URI 内联
    assert path == "/chat/completions"
    assert headers["Content-Type"] == "application/json"
    assert body["model"] == "qwen3.8-flash"
    assert body["response_format"] == {"type": "json_object"}
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text" and content[0]["text"] == "提示"
    assert content[1]["type"] == "video_url"
    assert content[1]["video_url"]["url"].startswith("data:video/mp4;base64,AAA")


def test_build_payload_qwen_custom_mime():
    _, _, body = v.build_payload("qwen", "m", "ZZZ", "p", mime="video/webm")
    url = body["messages"][0]["content"][1]["video_url"]["url"]
    assert url.startswith("data:video/webm;base64,ZZZ")


def test_build_payload_gemini():
    path, headers, body = v.build_payload("gemini", "gemini-3.8-flash", "BBB", "提示")
    # gemini 必须走原生 generateContent（OpenAI 兼容层会静默忽略视频）
    assert path.endswith("/v1beta/models/gemini-3.8-flash:generateContent")
    assert headers["Content-Type"] == "application/json"
    parts = body["contents"][0]["parts"]
    assert parts[0]["text"] == "提示"
    assert parts[1]["inlineData"]["data"] == "BBB"
    assert parts[1]["inlineData"]["mimeType"] == "video/mp4"
    assert body["generationConfig"]["responseMimeType"] == "application/json"


def test_build_payload_gemini_custom_mime():
    _, _, body = v.build_payload("gemini", "m", "B", "p", mime="video/quicktime")
    assert body["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "video/quicktime"


def test_build_payload_unknown_provider_raises():
    with pytest.raises(ValueError):
        v.build_payload("nope", "m", "A", "p")


def test_build_payload_provider_case_insensitive():
    assert v.build_payload("Qwen", "m", "A", "p")[0] == "/chat/completions"


# --------------------------------------------------------------------------- parse_structured


def test_parse_structured_fenced():
    assert v.parse_structured('```json\n{"a":1}\n```')["a"] == 1


def test_parse_structured_fenced_no_lang():
    assert v.parse_structured("```\n{\"a\": 2}\n```")["a"] == 2


def test_parse_structured_plain():
    assert v.parse_structured('{"a": 3}')["a"] == 3


def test_parse_structured_extracts_braces():
    # 模型夹带解释文字时，退而求其次抓最外层 JSON 对象
    assert v.parse_structured('好的，结果如下：\n{"a": 4}\n希望有帮助')["a"] == 4


def test_parse_structured_invalid_raises():
    with pytest.raises(Exception):
        v.parse_structured("完全不是 JSON")


# --------------------------------------------------------------------------- understand


def test_understand_qwen(monkeypatch, tmp_path):
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00\x01\x02\x03")
    cap: dict = {}
    monkeypatch.setattr(
        v,
        "_post_json",
        _fake(
            {
                "model": "qwen3.8-flash",
                "choices": [{"message": {"content": '{"summary":"s"}'}}],
                "usage": {
                    "prompt_tokens": 10,
                    "prompt_tokens_details": {"video_tokens": 2590},
                },
            },
            capture=cap,
        ),
    )
    out = asyncio.run(
        v.understand(
            str(vid),
            "分析一下",
            provider="qwen",
            base_url="https://api.xsai5.xyz/v1/",
            api_key="sk-1",
            model="qwen3.8-flash",
        )
    )
    assert out["text"] == '{"summary":"s"}'
    assert out["usage"]["prompt_tokens_details"]["video_tokens"] == 2590
    assert out["model"] == "qwen3.8-flash"
    assert out["provider"] == "qwen"
    # base 去尾斜杠后拼端点；Bearer 鉴权；视频为文件内容 base64
    assert cap["url"] == "https://api.xsai5.xyz/v1/chat/completions"
    assert cap["headers"]["Authorization"] == "Bearer sk-1"
    inline = cap["body"]["messages"][0]["content"][1]["video_url"]["url"]
    expected = base64.b64encode(b"\x00\x01\x02\x03").decode()
    assert inline == "data:video/mp4;base64," + expected


def test_understand_gemini(monkeypatch, tmp_path):
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"DATA")
    cap: dict = {}
    monkeypatch.setattr(
        v,
        "_post_json",
        _fake(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '{"summary":'}, {"text": '"g"}'}]}}
                ],
                "usageMetadata": {"promptTokenCount": 5},
            },
            capture=cap,
        ),
    )
    out = asyncio.run(
        v.understand(
            str(vid),
            "分析",
            provider="gemini",
            base_url="https://api.xsai5.xyz",
            api_key="g-key",
            model="gemini-3.8-flash",
        )
    )
    assert out["text"] == '{"summary":"g"}'
    assert out["usage"] == {"promptTokenCount": 5}
    assert out["provider"] == "gemini"
    assert cap["url"] == "https://api.xsai5.xyz/v1beta/models/gemini-3.8-flash:generateContent"
    assert cap["headers"]["x-goog-api-key"] == "g-key"


def test_understand_defaults_mime_from_extension(monkeypatch, tmp_path):
    vid = tmp_path / "clip.webm"
    vid.write_bytes(b"X")
    cap: dict = {}
    monkeypatch.setattr(
        v, "_post_json", _fake({"choices": [{"message": {"content": "ok"}}]}, capture=cap)
    )
    asyncio.run(
        v.understand(
            str(vid), "p", provider="qwen", base_url="http://x/v1", api_key="k", model="m"
        )
    )
    inline = cap["body"]["messages"][0]["content"][1]["video_url"]["url"]
    assert inline.startswith("data:video/webm;base64,")


def test_understand_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        asyncio.run(
            v.understand(
                str(tmp_path / "nope.mp4"),
                "p",
                provider="qwen",
                base_url="http://x/v1",
                api_key="k",
                model="m",
            )
        )


def test_understand_oversize_raises_before_network(monkeypatch, tmp_path):
    vid = tmp_path / "big.mp4"
    vid.write_bytes(b"\x00" * 2048)

    async def boom(url, headers, body, timeout):
        raise AssertionError("体积超限时不应发起请求")

    monkeypatch.setattr(v, "_post_json", boom)
    with pytest.raises(ValueError):
        asyncio.run(
            v.understand(
                str(vid),
                "p",
                provider="qwen",
                base_url="http://x/v1",
                api_key="k",
                model="m",
                max_mb=0.0005,  # ≈524 字节 < 2048 字节
            )
        )


def test_understand_passes_timeout(monkeypatch, tmp_path):
    vid = tmp_path / "c.mp4"
    vid.write_bytes(b"Y")
    cap: dict = {}
    monkeypatch.setattr(
        v, "_post_json", _fake({"choices": [{"message": {"content": "ok"}}]}, capture=cap)
    )
    asyncio.run(
        v.understand(
            str(vid), "p", provider="qwen", base_url="http://x/v1",
            api_key="k", model="m", timeout=123
        )
    )
    assert cap["timeout"] == 123


def test_understand_qwen_missing_choices_raises(monkeypatch, tmp_path):
    vid = tmp_path / "c.mp4"
    vid.write_bytes(b"Y")
    monkeypatch.setattr(v, "_post_json", _fake({"choices": []}))
    with pytest.raises(Exception):
        asyncio.run(
            v.understand(
                str(vid), "p", provider="qwen", base_url="http://x/v1",
                api_key="k", model="m"
            )
        )


def test_understand_gemini_missing_candidates_raises(monkeypatch, tmp_path):
    vid = tmp_path / "c.mp4"
    vid.write_bytes(b"Y")
    monkeypatch.setattr(v, "_post_json", _fake({"candidates": []}))
    with pytest.raises(Exception):
        asyncio.run(
            v.understand(
                str(vid), "p", provider="gemini", base_url="http://x",
                api_key="k", model="m"
            )
        )
