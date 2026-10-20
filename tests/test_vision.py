"""视频理解模块测试（payload 纯函数 / JSON 容错 / 统一网关注入，不联网）。

覆盖：
- `build_payload` 两种 provider（qwen OpenAI 兼容 `video_url` / gemini 原生 `inlineData`）的
  端点、body 结构与自定义 mime；未知 provider 抛错。
- `parse_structured` 对 Markdown 代码围栏、纯 JSON、夹带文本的容错与失败抛错。
- `understand` 读文件、体积上限，并且只能经注入的 ``ModelAccessService`` 回调调用。
"""

import asyncio
import base64
import types

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


# --------------------------------------------------------------------------- _join_provider_url


def test_join_provider_url_strips_v1_only_for_v1beta():
    # OpenAI 兼容端点：base 原样保留（含 /v1）
    assert (
        v._join_provider_url("https://api.xsai5.xyz/v1", "/chat/completions")
        == "https://api.xsai5.xyz/v1/chat/completions"
    )
    # gemini 原生端点：去掉末尾 /v1，拼到站点根
    assert (
        v._join_provider_url(
            "https://api.xsai5.xyz/v1", "/v1beta/models/g:generateContent"
        )
        == "https://api.xsai5.xyz/v1beta/models/g:generateContent"
    )
    # base 无 /v1：不误删
    assert (
        v._join_provider_url("https://api.xsai5.xyz", "/v1beta/models/g:generateContent")
        == "https://api.xsai5.xyz/v1beta/models/g:generateContent"
    )
    # 支持尾斜杠
    assert (
        v._join_provider_url("https://api.xsai5.xyz/v1/", "/v1beta/models/g:generateContent")
        == "https://api.xsai5.xyz/v1beta/models/g:generateContent"
    )


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

class _Gateway:
    def __init__(self, text, *, capture=None, fail=None):
        self.text = text
        self.capture = capture
        self.fail = fail

    async def __call__(self, *, request, parse_result, video_request=False, **kwargs):
        if self.capture is not None:
            self.capture.update({"request": request, "video_request": video_request})
        if self.fail is not None:
            raise self.fail
        return types.SimpleNamespace(data=parse_result(self.text), usage={})


def _understand(tmp_path, text='{"summary":"s"}', *, provider="qwen", capture=None, name="clip.mp4", max_mb=15, timeout=300, fail=None):
    vid = tmp_path / name
    vid.write_bytes(b"\x00\x01\x02\x03")
    if capture is None:
        capture = {}
    gateway = _Gateway(text, capture=capture, fail=fail)
    out = asyncio.run(
        v.understand(
            str(vid), "分析", provider=provider, max_mb=max_mb, timeout=timeout,
            call_model=gateway,
        )
    )
    return out, capture


def test_understand_qwen(tmp_path):
    out, cap = _understand(tmp_path)
    assert '"summary"' in out["text"]
    assert out["provider"] == "qwen"
    request = cap["request"]
    assert request["transport"] == "openai_video_url"
    expected = base64.b64encode(b"\x00\x01\x02\x03").decode()
    assert request["video_b64"] == expected
    assert request["mime"] == "video/mp4"
    assert cap["video_request"] is True


def test_understand_gemini(tmp_path):
    out, cap = _understand(tmp_path, provider="gemini")
    assert out["provider"] == "gemini"
    assert cap["request"]["transport"] == "gemini_inline"


def test_understand_defaults_mime_from_extension(tmp_path):
    _, cap = _understand(tmp_path, name="clip.webm")
    assert cap["request"]["mime"] == "video/webm"


def test_understand_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        asyncio.run(
            v.understand(
                str(tmp_path / "nope.mp4"), "p", provider="qwen",
                call_model=_Gateway("{}"),
            )
        )


def test_understand_oversize_raises_before_gateway(tmp_path):
    vid = tmp_path / "big.mp4"
    vid.write_bytes(b"\x00" * 2048)
    called = False

    async def gateway(**_kwargs):
        nonlocal called
        called = True

    with pytest.raises(ValueError):
        asyncio.run(
            v.understand(str(vid), "p", provider="qwen", max_mb=0.0005, call_model=gateway)
        )
    assert called is False


def test_understand_passes_timeout(tmp_path):
    _, cap = _understand(tmp_path, timeout=123)
    assert cap["request"]["timeout"] == 123


def test_understand_missing_gateway_is_programming_error(tmp_path):
    vid = tmp_path / "c.mp4"
    vid.write_bytes(b"Y")
    with pytest.raises(RuntimeError, match="ModelAccessService"):
        asyncio.run(v.understand(str(vid), "p", provider="qwen"))


def test_understand_unknown_provider_raises(tmp_path):
    with pytest.raises(ValueError):
        _understand(tmp_path, provider="nope")


def test_understand_invalid_json_raises(tmp_path):
    with pytest.raises(Exception):
        _understand(tmp_path, text="完全不是 JSON")


def test_understand_gateway_error_propagates(tmp_path):
    with pytest.raises(RuntimeError, match="upstream"):
        _understand(tmp_path, fail=RuntimeError("upstream 503"))
