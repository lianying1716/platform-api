"""模型协议适配器：请求构建、响应解析、错误分类与视频 transport 鉴权。"""

import httpx
import pytest

from app.models import AiProvider
from app.modules import model_protocols


def _provider(**kw) -> AiProvider:
    values = {
        "name": "p",
        "preset_key": "custom",
        "protocol": "openai",
        "base_url": "https://api.example.com/v1",
        "api_key_encrypted": "ciphertext",
    }
    values.update(kw)
    return AiProvider(**values)


def test_openai_chat_builds_bearer_request():
    provider = _provider()
    req = model_protocols.chat_request(
        provider,
        "gpt-x",
        {"messages": [{"role": "user", "content": "hi"}], "temperature": 0.2},
        api_key="sk-test",
    )
    assert req.url == "https://api.example.com/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer sk-test"
    assert req.body["model"] == "gpt-x"


def test_anthropic_chat_builds_native_request():
    provider = _provider(protocol="anthropic", base_url="https://api.anthropic.com")
    req = model_protocols.chat_request(
        provider,
        "claude-x",
        {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ],
            "temperature": 0.2,
        },
        api_key="sk-ant",
    )
    assert req.url == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-ant"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert req.body["system"] == "sys"
    assert req.body["messages"] == [{"role": "user", "content": "hi"}]


def test_gemini_chat_builds_native_request():
    provider = _provider(
        protocol="gemini", base_url="https://generativelanguage.googleapis.com"
    )
    req = model_protocols.chat_request(
        provider,
        "gemini-x",
        {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ]
        },
        api_key="goog",
    )
    assert req.url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    )
    assert req.headers["x-goog-api-key"] == "goog"
    assert req.body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert req.body["contents"][0]["parts"][0]["text"] == "hi"


def test_chat_parser_openai_anthropic_gemini():
    assert (
        model_protocols.parse_chat_response(
            "openai", {"choices": [{"message": {"content": "hello"}}]}
        )["text"]
        == "hello"
    )
    assert (
        model_protocols.parse_chat_response(
            "anthropic", {"content": [{"type": "text", "text": "hello"}]}
        )["text"]
        == "hello"
    )
    assert (
        model_protocols.parse_chat_response(
            "gemini",
            {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]},
        )["text"]
        == "hello"
    )


def test_video_request_uses_transport_not_provider_protocol():
    provider = _provider(protocol="openai", base_url="https://api.example.com/v1")
    req = model_protocols.video_request(
        provider,
        "gemini-video",
        "video-base64",
        "describe",
        transport="gemini_inline",
        api_key="goog",
    )
    assert req.url == (
        "https://api.example.com/v1beta/models/gemini-video:generateContent"
    )
    assert req.headers["x-goog-api-key"] == "goog"
    assert "Authorization" not in req.headers
    assert req.body["contents"][0]["parts"][1]["inlineData"]["data"] == "video-base64"


def test_list_models_request_and_parser_by_protocol():
    openai = model_protocols.list_models_request(_provider(), api_key="k")
    assert openai.url == "https://api.example.com/v1/models"
    assert openai.headers["Authorization"] == "Bearer k"
    # 模型清单是读取型接口，必须 GET：OpenAI 兼容网关只把 /models 注册在 GET 上，
    # 用 POST 会被路由层判 404（用户侧表现为"不论接哪家供应商都 404"）。
    assert openai.method == "GET"
    assert model_protocols.parse_models("openai", {"data": [{"id": "a"}]}) == ["a"]

    anthropic = model_protocols.list_models_request(
        _provider(protocol="anthropic", base_url="https://api.anthropic.com"), api_key="k"
    )
    assert anthropic.url == "https://api.anthropic.com/v1/models"
    assert anthropic.headers["x-api-key"] == "k"
    assert anthropic.method == "GET"
    assert model_protocols.parse_models("anthropic", {"data": [{"id": "b"}]}) == ["b"]

    gemini = model_protocols.list_models_request(
        _provider(
            protocol="gemini", base_url="https://generativelanguage.googleapis.com"
        ),
        api_key="k",
    )
    assert gemini.url == "https://generativelanguage.googleapis.com/v1beta/models"
    assert gemini.method == "GET"
    assert model_protocols.parse_models(
        "gemini", {"models": [{"name": "models/c"}]}
    ) == ["c"]


def test_send_request_uses_get_for_model_listing_and_post_for_chat(monkeypatch):
    """回归：模型清单必须以 GET 发出，对话仍为 POST。

    真实网关（OpenAI / Moonshot / OpenRouter / 星算AI）只在 GET 上提供 /models，
    用 POST 一律 404。这里从 transport 层钉住方法，避免只改 request 构建器却
    在 send_request 里被无条件 POST 覆盖。
    """
    import asyncio

    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "a"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(model_protocols.httpx, "AsyncClient", client_factory)

    listing = model_protocols.list_models_request(_provider(), api_key="k")
    listed = asyncio.run(model_protocols.send_request(listing, timeout=5))
    assert listed == {"data": [{"id": "a"}]}

    chat = model_protocols.chat_request(
        _provider(), "gpt-x", {"messages": [{"role": "user", "content": "hi"}]}, api_key="k"
    )
    asyncio.run(model_protocols.send_request(chat, timeout=5))

    assert seen == [("GET", "/v1/models"), ("POST", "/v1/chat/completions")]


def test_classify_error_http_and_network():
    req = httpx.Request("GET", "https://x")
    resp_401 = httpx.Response(401, request=req)
    assert model_protocols.classify_error(
        httpx.HTTPStatusError("unauthorized", request=req, response=resp_401)
    ) == "auth"
    resp_500 = httpx.Response(503, request=req)
    assert model_protocols.classify_error(
        httpx.HTTPStatusError("server", request=req, response=resp_500)
    ) == "server"
    assert model_protocols.classify_error(httpx.ConnectTimeout("timeout")) == "timeout"
    assert model_protocols.classify_error(ValueError("bad")) == "unknown"


def test_normalize_transport_and_auth_style():
    assert model_protocols.normalize_transport("gemini", None) == "gemini_inline"
    assert model_protocols.normalize_transport("openai", None) == "openai_video_url"
    assert model_protocols.auth_style_for_transport("gemini_inline") == "gemini_api_key"
    assert model_protocols.auth_style_for_transport("openai_video_url") == "bearer"
    with pytest.raises(ValueError):
        model_protocols.normalize_transport("openai", "bogus")
