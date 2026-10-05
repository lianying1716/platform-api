"""判类 Pass 0：按标题/作者/时长/转录文本给视频分型（便宜、纯文本）。

两遍式视频理解的第一遍：先判类型 → 服务层再选该类型的专属提示词。
判类只看文本（默认），失败由调用方回退通用模板，不阻断后续理解。

对外能力：
- `build_messages(...) -> (system_prompt, user_content)`：纯函数，拼装提示词。
- `classify(...) -> {"type","confidence","reason","signals"}`：异步调用 LLM。
  - 返回类型不在 `taxonomy` 枚举内 → `type="other"`, `confidence=0`。
  - 网络/解析失败 → 抛异常（调用方回退通用模板）。

测试注入点：内部 `_post_json(url, headers, body, timeout)` 用 `httpx.AsyncClient`；
`classify` 只经它发请求，测试 monkeypatch `_post_json`，不要 patch httpx。
"""

import base64
import json
import logging
import mimetypes
from pathlib import Path

import httpx

logger = logging.getLogger("platform.classify")

# 转录送入分类器的最大字符数（设计 §4「前 ~600 字」）。
_TRANSCRIPT_LIMIT = 600

_SYSTEM_TEMPLATE = (
    "你是短视频内容分类助手。根据标题、作者、时长与转录文本，判断视频属于下列哪一类。\n"
    "可选类型（枚举 -> 含义）：\n__TAXONOMY__\n"
    "判断规则：\n"
    "1. 只依据给定文本，不要臆测画面细节；\n"
    "2. 必须且只能选择一个枚举值（无法归入任何一类时选 other）；\n"
    "3. confidence 为 0~1 的浮点数；\n"
    "4. signals 列出你命中的关键线索（短词），reason 用一句话说明理由。\n"
    "只输出 JSON，不要解释、不要 Markdown 代码块。\n"
    '输出格式：{"type": "<枚举>", "confidence": 0.0, "reason": "", "signals": []}'
)


def build_messages(
    title: str,
    author: str,
    duration_sec: float | int | None,
    transcript: str,
    taxonomy: dict,
) -> tuple[str, str]:
    """拼装 (system_prompt, user_content) 纯函数。

    taxonomy 为「枚举 -> 中文」映射，直接写进系统提示词约束模型只能选枚举内值。
    """
    lines = [f"- {key}：{name}" for key, name in taxonomy.items()]
    # 用 replace 而非 str.format：模板含 JSON 字面量花括号（{"type":...}），format 会误当占位符。
    system = _SYSTEM_TEMPLATE.replace("__TAXONOMY__", "\n".join(lines))

    text = (transcript or "").strip()[:_TRANSCRIPT_LIMIT]
    user = (
        f"标题：{title or '（无）'}\n"
        f"作者：{author or '（无）'}\n"
        f"时长（秒）：{duration_sec if duration_sec is not None else '未知'}\n"
        f"转录文本：\n{text or '（无）'}"
    )
    return system, user


def _cover_data_uri(cover_path: str) -> str | None:
    """把封面图读为 data URI；读不到或空文件返回 None（判类退化为纯文本）。"""
    try:
        raw = Path(cover_path).read_bytes()
    except OSError:
        logger.warning("判类封面读取失败，退化为纯文本: %s", cover_path)
        return None
    if not raw:
        return None
    mime = mimetypes.guess_type(cover_path)[0] or "image/jpeg"
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _coerce_confidence(value: object) -> float:
    """归一置信度到 [0,1]；bool/非数值/非法一律 0.0。"""
    if isinstance(value, bool):
        return 0.0
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if num != num:  # NaN
        return 0.0
    return max(0.0, min(1.0, num))


def _coerce_signals(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s for s in value if isinstance(s, str)]


async def _post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    """发一次 JSON POST 并返回响应 JSON；非 2xx 抛 httpx.HTTPStatusError。"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()


async def classify(
    title: str,
    author: str,
    duration_sec: float | int | None,
    transcript: str,
    taxonomy: dict,
    *,
    cover_path: str | None,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 30,
    use_cover: bool = False,
) -> dict:
    """判类 Pass 0。

    Args:
        title/author/duration_sec/transcript: 文本信号（缺失用空跳过）。
        taxonomy: 枚举 -> 中文；不在其中的类型统一归 ``other``。
        cover_path: 可选封面路径；仅当 ``use_cover=True`` 且可读时随文本一并送入。
        base_url/api_key/model: OpenAI 兼容端点配置（服务层从 DB/设置解析后注入）。
        timeout: 单次请求超时秒数。

    Returns:
        ``{"type": str, "confidence": float, "reason": str, "signals": list[str]}``

    Raises:
        httpx.HTTPError: 网络/HTTP 失败。
        KeyError/ValueError/json.JSONDecodeError: 响应结构或内容无法解析。
    """
    base = (base_url or "").rstrip("/")
    system, user_text = build_messages(title, author, duration_sec, transcript, taxonomy)

    content: object = user_text
    if use_cover and cover_path:
        data_uri = _cover_data_uri(cover_path)
        if data_uri:
            content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = await _post_json(f"{base}/chat/completions", headers, body, timeout)

    raw = data["choices"][0]["message"]["content"]
    parsed = json.loads(raw)  # 解析失败 → 抛异常，由调用方回退通用模板

    candidate = parsed.get("type")
    if isinstance(candidate, str) and candidate in taxonomy:
        return {
            "type": candidate,
            "confidence": _coerce_confidence(parsed.get("confidence")),
            "reason": parsed.get("reason") if isinstance(parsed.get("reason"), str) else "",
            "signals": _coerce_signals(parsed.get("signals")),
        }
    reason = parsed.get("reason") if isinstance(parsed.get("reason"), str) else ""
    if not reason:
        reason = f"模型返回未知类型 {candidate!r}，归为 other"
    return {"type": "other", "confidence": 0.0, "reason": reason, "signals": []}
