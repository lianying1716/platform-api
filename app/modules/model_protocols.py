"""模型协议适配：把统一请求翻译成 OpenAI / Anthropic / Gemini 原生格式。

业务模块只构造「messages + options」这一种请求，不感知供应商协议；本模块负责
端点、鉴权头、请求体与响应解析。视频输入是单独 transport，不与对话协议绑定。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

_V1_SUFFIX_RE = re.compile(r"/v1/?$")
_DATA_URI_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.*)$", re.DOTALL)

_PROTOCOLS = {"openai", "anthropic", "gemini"}
_VIDEO_TRANSPORTS = {"openai_video_url", "gemini_inline"}
_AUTH_STYLES = {"openai_video_url": "bearer", "gemini_inline": "gemini_api_key"}


class ModelProtocolError(RuntimeError):
    """协议或响应结构错误；``error_class`` 供调用审计使用。"""

    error_class = "unknown"

    def __init__(self, message: str, *, error_class: str | None = None):
        super().__init__(message)
        if error_class:
            self.error_class = error_class


class UnsupportedCapabilityError(ModelProtocolError):
    error_class = "unsupported_capability"


class EmptyResponseError(ModelProtocolError):
    error_class = "empty_response"


class InvalidJSONError(ModelProtocolError):
    error_class = "invalid_json"


class SchemaMismatchError(ModelProtocolError):
    error_class = "schema_mismatch"


@dataclass(frozen=True)
class ProtocolRequest:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    # 模型清单等读取型接口是 GET；对话/视频是 POST。默认 POST，保持既有调用点语义不变。
    method: str = "POST"


def _normalize_protocol_name(value: object) -> str:
    return str(value or "").strip().lower()


def resolve_protocol(model, provider) -> str:
    """生效协议：模型级覆盖优先，未设置则继承供应商。

    `model.protocol` 为 None 表示「跟随供应商」（存量数据与新导入模型的默认）。
    这样网关同时提供多协议时可按模型各自落定，而不用把协议钉在供应商上。
    """
    override = _normalize_protocol_name(getattr(model, "protocol", None))
    inherited = _normalize_protocol_name(getattr(provider, "protocol", None))
    return override or inherited


def validate_protocol(value: object) -> str:
    """校验并归一协议名；非法值抛 ``ModelProtocolError``。"""
    name = _normalize_protocol_name(value)
    if name not in _PROTOCOLS:
        raise ModelProtocolError(f"不支持的模型协议: {name!r}", error_class="bad_request")
    return name


def _protocol(provider) -> str:
    return validate_protocol(getattr(provider, "protocol", ""))


def normalize_transport(protocol: str | None, transport: str | None) -> str:
    """把视频 transport 归一；未指定时按供应商协议给默认值。"""
    value = (transport or "").strip().lower()
    if not value:
        return "gemini_inline" if (protocol or "").strip().lower() == "gemini" else "openai_video_url"
    if value not in _VIDEO_TRANSPORTS:
        raise ValueError(f"不支持的视频 transport: {transport!r}")
    return value


def auth_style_for_transport(transport: str) -> str:
    """视频鉴权风格由 transport 派生，不读取独立配置。"""
    normalized = normalize_transport("openai", transport)
    return _AUTH_STYLES[normalized]


def _root(base_url: str) -> str:
    return (base_url or "").rstrip("/")


def _v1_root(base_url: str) -> str:
    return _V1_SUFFIX_RE.sub("", _root(base_url))


def _openai_url(base_url: str, path: str) -> str:
    return f"{_root(base_url)}{path}"


def _anthropic_url(base_url: str, path: str) -> str:
    return f"{_v1_root(base_url)}{path}"


def _gemini_url(base_url: str, path: str) -> str:
    return f"{_v1_root(base_url)}{path}"


def _merge_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """抽出 system 消息，返回 (system, 其余消息)；保留原顺序。"""
    system_parts: list[str] = []
    rest: list[dict] = []
    for message in messages or []:
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        if role == "system":
            system_parts.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        else:
            rest.append(message)
    return "\n\n".join(p for p in system_parts if p), rest


def _data_uri_parts(uri: str) -> tuple[str, str] | None:
    match = _DATA_URI_RE.match(uri or "")
    if not match:
        return None
    return match.group("mime"), match.group("data")


def _openai_content(content: object) -> object:
    return content


def _anthropic_content(content: object) -> object:
    """把 OpenAI 风格的 text/image_url 内容转成 Anthropic content blocks。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    blocks: list[dict] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            blocks.append({"type": "text", "text": str(item.get("text", ""))})
        elif kind == "image_url":
            url = item.get("image_url", {}).get("url", "") if isinstance(item.get("image_url"), dict) else ""
            parts = _data_uri_parts(str(url))
            if parts is None:
                raise UnsupportedCapabilityError("Anthropic 图片输入需要 base64 data URI")
            mime, data = parts
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        elif kind == "image" and isinstance(item.get("source"), dict):
            blocks.append(item)
    return blocks


def _gemini_parts(content: object) -> list[dict]:
    if isinstance(content, str):
        return [{"text": content}] if content else []
    if not isinstance(content, list):
        return [{"text": str(content)}]
    parts: list[dict] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append({"text": str(item.get("text", ""))})
        elif kind == "image_url":
            url = item.get("image_url", {}).get("url", "") if isinstance(item.get("image_url"), dict) else ""
            parsed = _data_uri_parts(str(url))
            if parsed is None:
                raise UnsupportedCapabilityError("Gemini 图片输入需要 base64 data URI")
            mime, data = parsed
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
        elif kind == "inlineData":
            parts.append(item)
    return parts


def chat_request(
    provider, model: str, request: dict, *, api_key: str, protocol: str | None = None
) -> ProtocolRequest:
    """构建一次对话请求；request 使用统一的 OpenAI 风格 messages。

    ``protocol`` 显式传入时优先（调用方用 ``resolve_protocol(model, provider)`` 取模型级
    协议）；不传则回退供应商协议，保持既有调用点语义。
    """
    protocol = validate_protocol(protocol) if protocol else _protocol(provider)
    body = dict(request or {})
    messages = body.pop("messages", [])
    body["model"] = model

    if protocol == "openai":
        body["messages"] = [
            {**m, "content": _openai_content(m.get("content", ""))} for m in messages
        ]
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return ProtocolRequest(_openai_url(provider.base_url, "/chat/completions"), headers, body)

    system, rest = _merge_system(messages)
    if protocol == "anthropic":
        converted = [
            {**m, "content": _anthropic_content(m.get("content", ""))} for m in rest
        ]
        body.pop("response_format", None)
        body.setdefault("max_tokens", 4096)
        body["messages"] = converted
        if system:
            body["system"] = system
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
        }
        return ProtocolRequest(_anthropic_url(provider.base_url, "/v1/messages"), headers, body)

    # Gemini 原生：system 独立，content 使用 contents/parts。
    contents: list[dict] = []
    for message in rest:
        role = "model" if message.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": _gemini_parts(message.get("content", ""))})
    generation_config: dict[str, Any] = {}
    if "temperature" in body:
        generation_config["temperature"] = body.pop("temperature")
    if "max_tokens" in body:
        generation_config["maxOutputTokens"] = body.pop("max_tokens")
    if body.pop("response_format", None):
        generation_config["responseMimeType"] = "application/json"
    gemini_body: dict[str, Any] = {"contents": contents}
    if system:
        gemini_body["systemInstruction"] = {"parts": [{"text": system}]}
    if generation_config:
        gemini_body["generationConfig"] = generation_config
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    return ProtocolRequest(
        _gemini_url(
            provider.base_url,
            f"/v1beta/models/{model}:generateContent",
        ),
        headers,
        gemini_body,
    )


def video_request(
    provider,
    model: str,
    video_b64: str,
    prompt: str,
    *,
    transport: str,
    api_key: str,
    mime: str = "video/mp4",
    protocol: str | None = None,
) -> ProtocolRequest:
    """按视频 transport 构建请求；鉴权只由 transport 决定。"""
    effective = protocol or getattr(provider, "protocol", None)
    name = normalize_transport(effective, transport)
    if name == "gemini_inline":
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": mime, "data": video_b64}},
                    ]
                }
            ],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        }
        return ProtocolRequest(
            _gemini_url(
                provider.base_url,
                f"/v1beta/models/{model}:generateContent",
            ),
            {"Content-Type": "application/json", "x-goog-api-key": api_key},
            body,
        )

    data_uri = f"data:{mime};base64,{video_b64}"
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "video_url", "video_url": {"url": data_uri}},
                ],
            }
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    return ProtocolRequest(
        _openai_url(provider.base_url, "/chat/completions"),
        {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        body,
    )


def list_models_request(provider, *, api_key: str) -> ProtocolRequest:
    """构建供应商模型清单请求。

    三家协议都是读取型接口，必须用 GET：OpenAI 兼容网关（OpenAI、DeepSeek、
    Moonshot、OpenRouter、星算AI 等）只把 ``/models`` 注册在 GET 上，用 POST 会被
    路由层直接判 404；Anthropic ``/v1/models`` 与 Gemini ``/v1beta/models`` 同理。
    """
    protocol = _protocol(provider)
    if protocol == "openai":
        return ProtocolRequest(
            _openai_url(provider.base_url, "/models"),
            {"Authorization": f"Bearer {api_key}"} if api_key else {},
            {},
            method="GET",
        )
    if protocol == "anthropic":
        return ProtocolRequest(
            _anthropic_url(provider.base_url, "/v1/models"),
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            {},
            method="GET",
        )
    return ProtocolRequest(
        _gemini_url(provider.base_url, "/v1beta/models"),
        {"x-goog-api-key": api_key},
        {},
        method="GET",
    )


def parse_models(protocol: str, data: dict) -> list[str]:
    """解析各协议模型清单响应，去重且保持原顺序。"""
    name = (protocol or "").strip().lower()
    out: list[str] = []
    if name in ("openai", "anthropic"):
        items = data.get("data") if isinstance(data, dict) else None
        for item in items or []:
            if isinstance(item, dict) and item.get("id"):
                out.append(str(item["id"]))
    elif name == "gemini":
        for item in (data.get("models") if isinstance(data, dict) else None) or []:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("name") or "")
            if raw.startswith("models/"):
                raw = raw[len("models/") :]
            if raw:
                out.append(raw)
    seen: set[str] = set()
    return [key for key in out if not (key in seen or seen.add(key))]


def parse_chat_response(protocol: str, data: dict) -> dict:
    """把各协议响应归一为 ``{"text", "usage", "model"}``。"""
    name = (protocol or "").strip().lower()
    model = data.get("model", "") if isinstance(data, dict) else ""
    if name == "openai":
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise EmptyResponseError("模型响应没有 choices")
        text = (choices[0].get("message") or {}).get("content")
        if not isinstance(text, str) or not text.strip():
            raise EmptyResponseError("模型响应内容为空")
        return {"text": text, "usage": data.get("usage") or {}, "model": model}
    if name == "anthropic":
        blocks = data.get("content") if isinstance(data, dict) else None
        if not isinstance(blocks, list):
            raise EmptyResponseError("模型响应没有 content")
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        if not text.strip():
            raise EmptyResponseError("模型响应内容为空")
        return {"text": text, "usage": data.get("usage") or {}, "model": model}
    if name == "gemini":
        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not candidates:
            raise EmptyResponseError("模型响应没有 candidates")
        parts = ((candidates[0].get("content") or {}).get("parts")) if isinstance(candidates[0], dict) else None
        if not isinstance(parts, list):
            raise EmptyResponseError("模型响应没有 parts")
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text.strip():
            raise EmptyResponseError("模型响应内容为空")
        return {"text": text, "usage": data.get("usageMetadata") or {}, "model": model}
    raise ModelProtocolError(f"不支持的模型协议: {protocol!r}", error_class="bad_request")


def classify_http_status(status_code: int) -> str:
    if status_code in (401, 403):
        return "auth"
    if status_code == 429:
        return "rate_limit"
    if 500 <= status_code <= 599:
        return "server"
    if 400 <= status_code <= 499:
        return "bad_request"
    return "unknown"


def classify_error(exc: BaseException) -> str:
    """把异常映射为稳定的 error_class 枚举。"""
    if isinstance(exc, ModelProtocolError):
        return exc.error_class
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return classify_http_status(exc.response.status_code)
    if isinstance(exc, httpx.TransportError):
        return "network"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    return "unknown"


def http_status_of(exc: BaseException) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


async def send_request(request: ProtocolRequest, *, timeout: float) -> dict:
    """发送一次请求并返回响应 JSON；非 2xx 抛 ``httpx.HTTPStatusError``。

    GET（模型清单）不带请求体；POST（对话/视频）发送 JSON body。
    """
    method = (request.method or "POST").upper()
    async with httpx.AsyncClient(timeout=timeout) as client:
        if method == "GET":
            response = await client.get(request.url, headers=request.headers)
        else:
            response = await client.post(request.url, headers=request.headers, json=request.body)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise InvalidJSONError("上游返回不是 JSON") from exc
    if not isinstance(data, dict):
        raise SchemaMismatchError("上游响应不是 JSON 对象")
    return data
