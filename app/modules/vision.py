"""视频理解 provider 适配器：把视频内联进多模态请求并解析结构化结果。

为什么需要「适配器」而不是一套通用 payload（见 `docs/plans/2026-10-05-video-understanding-tier-research.md`
与 `docs/plans/evidence/2026-10-05-xsai-video-input-probe.md`）：

- `qwen`（nexai `qwen3.8-flash` 等）走 **OpenAI 兼容** `POST {base}/chat/completions`，
  content 项形如 `{"type":"video_url","video_url":{"url":"data:video/mp4;base64,..."}}`。
- `gemini`（`gemini-3.8-flash`）**只有原生** `POST {base}/v1beta/models/{model}:generateContent`
  才真读视频，part 形如 `{"inlineData":{"mimeType":"video/mp4","data":<base64>}}`；
  其 OpenAI 兼容路径会**静默忽略**视频并产生幻觉，故不复用。

对外能力：
- `build_payload(provider, model, video_b64, prompt, *, mime) -> (path, headers, body)`：纯函数，
  返回端点**相对路径**（不含 base）、鉴权头的静态部分与请求体，便于单测不联网。
- `parse_structured(text) -> dict`：纯函数，容错解析模型返回的 JSON（去代码围栏 / 抓最外层花括号）。
- `understand(video_path, prompt, *, provider, base_url, api_key, model, max_mb, timeout) -> dict`：
  读文件 -> 体积校验 -> 拼 payload -> 发请求 -> 提取文本与 usage。
  - 体积超 `max_mb` -> `ValueError`（由服务层先压缩，不在此隐式降质）。
  - 文件缺失 -> `FileNotFoundError`；网络/结构失败 -> 抛异常（由 worker 重试）。

**base_url 约定**：遵循 **OpenAI 兼容约定、包含 `/v1`**（如 `https://api.xsai5.xyz/v1`），
与 `classify` / `analysis` 一致。qwen 用 `{base_url}/chat/completions`；gemini 的原生端点自带
`/v1beta`，由 `_join_provider_url` 归一拼到站点根（去掉末尾 `/v1`），避免 `.../v1/v1beta/...`。

测试注入点：内部 `_post_json(url, headers, body, timeout)` 用 `httpx.AsyncClient`；
`understand` 只经它发请求，测试 monkeypatch `_post_json`，不要 patch httpx。
"""

import base64
import json
import mimetypes
import re
from pathlib import Path

import httpx

_MB = 1024 * 1024

# 供应商别名 -> 规范名（大小写不敏感，便于设置页填 "Qwen"/"gemini" 都行）。
_PROVIDER_ALIASES = {
    "qwen": "qwen",
    "dashscope": "qwen",
    "openai": "qwen",
    "openai-compatible": "qwen",
    "gemini": "gemini",
    "google": "gemini",
}

# 去掉 ```json ... ``` / ``` ... ``` 代码围栏，并抓取最外层 JSON 对象。
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")

# base_url 末尾的 `/v1`（OpenAI 兼容约定，可能带尾斜杠）——用于 gemini 原生端点归一。
_V1_SUFFIX_RE = re.compile(r"/v1/?$")


def _normalize_provider(provider: str) -> str:
    key = (provider or "").strip().lower()
    name = _PROVIDER_ALIASES.get(key)
    if name is None:
        raise ValueError(f"未知的视频理解 provider: {provider!r}（支持 qwen / gemini）")
    return name


def _resolve_mime(video_path: str, mime: str | None) -> str:
    """确定内联视频的 MIME：显式入参优先，否则按文件扩展名猜，再兜底 video/mp4。"""
    if mime:
        return mime
    guessed = mimetypes.guess_type(str(video_path))[0]
    if guessed and guessed.startswith("video/"):
        return guessed
    return "video/mp4"


def _join_provider_url(base_url: str, path: str) -> str:
    """按 provider 端点语义拼接最终 URL。

    `base_url` 遵循 **OpenAI 兼容约定、包含 `/v1`**（如 `https://api.xsai5.xyz/v1`），
    与 classify/analysis 一致。但两 provider 的端点前缀不同：

    - qwen 走 OpenAI 兼容端点（`/chat/completions`）→ 直接用 `base_url` 拼接。
    - gemini 走 Google **原生**端点（`/v1beta/models/{model}:generateContent`）→ 端点
      自带 `/v1beta`，必须拼到**站点根**上；否则会出现错误的 `.../v1/v1beta/...`。

    站点根 = `base_url` 去掉末尾的 `/v1`（兼容 `.../v1` 与 `.../v1/`）；若 base 不含
    `/v1` 则原样作为根（不重复追加、不误删）。
    """
    base = (base_url or "").rstrip("/")
    if path.startswith("/v1beta"):
        root = _V1_SUFFIX_RE.sub("", base)
        return f"{root}{path}"
    return f"{base}{path}"


def build_payload(
    provider: str,
    model: str,
    video_b64: str,
    prompt: str,
    *,
    mime: str = "video/mp4",
) -> tuple[str, dict, dict]:
    """按 provider 拼装请求（端点相对路径、鉴权头、body）。

    Args:
        provider: `qwen` | `gemini`（含常见别名，大小写不敏感）。
        model: 模型名（不写死，运行时由设置注入）。
        video_b64: 视频内容的 base64（不含 data URI 前缀）。
        prompt: 用户提示词文本。
        mime: 视频 MIME 类型。

    Returns:
        `(path, headers, body)`；`path` 为相对路径（`understand` 用 base_url 拼接），
        `headers` 只含静态公共头，鉴权头由 `understand` 注入。

    Raises:
        ValueError: provider 未知。
    """
    name = _normalize_provider(provider)
    if name == "qwen":
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
        return "/chat/completions", {"Content-Type": "application/json"}, body

    # gemini 原生 generateContent
    path = f"/v1beta/models/{model}:generateContent"
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
    return path, {"Content-Type": "application/json"}, body


def parse_structured(text: str) -> dict:
    """容错解析模型返回的 JSON 对象。

    依次尝试：直接解析 -> 去 Markdown 代码围栏后解析 -> 截取第一个 `{` 到最后一个 `}` 解析。
    全部失败则抛 `json.JSONDecodeError`（由调用方决定是重试还是落错误状态）。
    """
    if not isinstance(text, str):
        raise ValueError(f"视频理解返回文本类型非法: {type(text).__name__}")
    raw = text.strip()

    candidate = _FENCE_RE.sub("", raw).strip()
    for attempt in (raw, candidate):
        try:
            parsed = json.loads(attempt)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed

    # 退而求其次：抓最外层花括号（模型夹带解释文字时）
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        parsed = json.loads(candidate[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise json.JSONDecodeError("视频理解返回内容不是合法 JSON 对象", candidate, 0)


async def _post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    """发一次 JSON POST 并返回响应 JSON；非 2xx 抛 httpx.HTTPStatusError。"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()


def _extract_qwen_text(data: dict) -> str:
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("qwen 返回 message.content 不是字符串")
    return content


def _extract_gemini_text(data: dict) -> str:
    parts = data["candidates"][0]["content"]["parts"]
    if not isinstance(parts, list):
        raise ValueError("gemini 返回 parts 结构非法")
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


async def understand(
    video_path: str,
    prompt: str,
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    max_mb: float = 15,
    timeout: float = 300,
    mime: str | None = None,
) -> dict:
    """把视频内联给多模态模型做一次理解，返回文本与用量。

    Args:
        video_path: 本地视频路径。
        prompt: 该视频类型的理解提示词（由服务层按类型拼装）。
        provider/base_url/api_key/model: 供应商适配配置（服务层从设置解析后注入）。
        max_mb: 内联体积上限；超过则抛 `ValueError`，由服务层先压缩再重试。
        timeout: 单次请求超时秒数（视频较大，默认给足 300s）。
        mime: 覆盖 MIME 推断（默认按扩展名，再兜底 video/mp4）。

    Returns:
        `{"text": str, "usage": dict, "model": str, "provider": str}`

    Raises:
        FileNotFoundError: 视频文件不存在。
        ValueError: provider 未知 / 体积超限。
        httpx.HTTPError: 网络或 HTTP 失败。
        KeyError/ValueError/TypeError: 响应结构不符合预期。
    """
    name = _normalize_provider(provider)
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(f"待理解视频不存在: {video_path}")

    raw = src.read_bytes()
    if max_mb and len(raw) > max_mb * _MB:
        raise ValueError(
            f"视频体积 {len(raw) / _MB:.2f}MB 超过上限 {max_mb}MB，请先压缩: {video_path}"
        )

    mime_type = _resolve_mime(video_path, mime)
    video_b64 = base64.b64encode(raw).decode("ascii")
    path, headers, body = build_payload(name, model, video_b64, prompt, mime=mime_type)

    if name == "qwen":
        headers = {**headers, "Authorization": f"Bearer {api_key}"}
    else:
        headers = {**headers, "x-goog-api-key": api_key}

    data = await _post_json(_join_provider_url(base_url, path), headers, body, timeout)

    if name == "qwen":
        text = _extract_qwen_text(data)
    else:
        text = _extract_gemini_text(data)
    usage = data.get("usage") if name == "qwen" else data.get("usageMetadata")

    return {
        "text": text,
        "usage": usage if isinstance(usage, dict) else {},
        "model": model,
        "provider": name,
    }
