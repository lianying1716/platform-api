"""爆款拆解模块：提示词构造 + LLM 调用 + JSON 解析。

职责（纯模块层，不碰 DB；DB 拼装/落库在 `app/services/breakdown_service.py`）：

- `build_breakdown_prompt(payload) -> (system, user)`：把「元数据 + 转录 + 视频理解(L3) +
  评论摘要 + 后端算好的互动数字」拼成 LLM 的 system/user，纯函数便于单测。
- `run_llm(system, user, *, base_url, api_key, model, timeout) -> dict`：走 OpenAI 兼容
  `chat/completions`，强制 `response_format=json_object`，容错解析严格 JSON；非法输出抛异常
  （由 worker 落 `status=error` / 重试，不静默降级）。

红线：
- 数字（相对中位数倍数/互动率/分位）由后端确定性计算后作为**输入**交给模型，模型只做定性，
  禁止重算或杜撰（提示词里已写明）。
- 供应商可换、模型名/base_url 来自配置，不硬编码。

测试注入点：内部 `_post_json(url, headers, body, timeout)` 用 `httpx.AsyncClient`；
`run_llm` 只经它发请求，测试 monkeypatch `_post_json`，不要 patch httpx。
"""

import json
import re

import httpx

from app.core.breakdown_settings import DEFAULT_BREAKDOWN_PROMPT

# 转录送入拆解的最大字符数（与 analysis.py 的上限对齐，控制 token）。
_TRANSCRIPT_LIMIT = 8000
_COMMENT_LIMIT = 30

# 去掉 ```json ... ``` / ``` ... ``` 代码围栏（与 vision.py 同款，保持容错一致）。
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")


def _fmt(value: object, fallback: str = "（无）") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _dump(value: object) -> str:
    """紧凑 JSON 文本（中文不转义）；非 JSON 可序列化对象退化为 str。"""
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def build_breakdown_prompt(payload: dict) -> tuple[str, str]:
    """拼装拆解提示词，返回 ``(system, user)``。

    ``payload`` 字段（均容错，缺失按「（无）」/空处理）::

        platform, title, author, duration_sec, published_at,
        play, like, comment,
        data_context,      # 后端确定性算好的数字（相对中位数倍数/互动率/分位）
        transcript,        # transcript.full_text
        timeline,          # 视频理解 timeline（L3）
        understanding,     # 视频理解 {summary, dimensions, ...}（L3）
        comments,          # Top-N 评论正文（list[str]）

    system 提示词优先取 payload["prompt"]（服务层从设置读取，可在线改），否则用内置默认。
    """
    system = payload.get("prompt")
    if not isinstance(system, str) or not system.strip():
        system = DEFAULT_BREAKDOWN_PROMPT

    # 互动数字：既列当前值，也列后端算好的上下文（相对中位数倍数/互动率）。
    metric_lines = [
        f"播放：{_fmt(payload.get('play'), '未知')}",
        f"点赞：{_fmt(payload.get('like'), '未知')}",
        f"评论：{_fmt(payload.get('comment'), '未知')}",
    ]
    data_context = payload.get("data_context")
    if isinstance(data_context, dict) and data_context:
        metric_lines.append(f"确定性上下文（后端算好，只引用不重算）：{_dump(data_context)}")

    transcript = (payload.get("transcript") or "").strip()[:_TRANSCRIPT_LIMIT]
    comment_lines = [str(c) for c in (payload.get("comments") or []) if str(c).strip()][:_COMMENT_LIMIT]

    understanding = payload.get("understanding")
    if isinstance(understanding, dict) and understanding:
        understanding_text = _dump(understanding)
    else:
        understanding_text = "（无视频理解结果，请仅依据转录与评论分析，不要臆测画面）"

    timeline = payload.get("timeline")
    timeline_text = _dump(timeline) if timeline else "（无）"

    user = (
        "【作品元数据】\n"
        f"平台：{_fmt(payload.get('platform'))}\n"
        f"标题：{_fmt(payload.get('title'))}\n"
        f"作者：{_fmt(payload.get('author'))}\n"
        f"时长（秒）：{_fmt(payload.get('duration_sec'), '未知')}\n"
        f"发布时间：{_fmt(payload.get('published_at'))}\n"
        "\n【互动数据】\n"
        + "\n".join(metric_lines)
        + "\n\n【转录文案】\n"
        + (transcript or "（无转录，请仅依据其他材料分析）")
        + "\n\n【视频理解（L3）】\n"
        + understanding_text
        + "\n\n【时间轴】\n"
        + timeline_text
        + "\n\n【评论区 Top 摘要】\n"
        + ("\n".join(f"- {c}" for c in comment_lines) if comment_lines else "（无评论摘要）")
    )
    return system, user


async def _post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    """发一次 JSON POST 并返回响应 JSON；非 2xx 抛 httpx.HTTPStatusError。"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()


def parse_structured(text: str) -> dict:
    """容错解析模型返回的 JSON 对象。

    依次尝试：直接解析 -> 去 Markdown 代码围栏后解析 -> 截取第一个 `{` 到最后一个 `}`。
    全部失败抛 `ValueError`（调用方决定落 error / 重试）。
    """
    if not isinstance(text, str):
        raise ValueError(f"拆解返回文本类型非法: {type(text).__name__}")
    raw = text.strip()
    candidate = _FENCE_RE.sub("", raw).strip()
    for attempt in (raw, candidate):
        try:
            parsed = json.loads(attempt)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(candidate[start : end + 1])
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("拆解返回内容不是合法 JSON 对象")


async def run_llm(
    system: str,
    user: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 90,
) -> dict:
    """调 OpenAI 兼容 `chat/completions`，返回 ``{"data", "usage", "model"}``。

    Args:
        system/user: `build_breakdown_prompt` 产出的提示词。
        base_url: OpenAI 兼容约定、**包含 `/v1`**（如 `https://api.xsai5.xyz/v1`）。
        api_key/model: 供应商配置（服务层从 DB 设置优先解析后注入）。
        timeout: 单次请求超时秒数（拆解为长文本，默认 90s）。

    Returns:
        dict，字段：
        - ``data``：解析后的严格 JSON 对象（报告本体）。
        - ``usage``：接口返回的 token 用量（缺省为空 dict）。
        - ``model``：实际使用的模型名（响应未回带时取入参 ``model``）。

    Raises:
        httpx.HTTPError: 网络 / HTTP 失败。
        ValueError: 返回内容不是合法 JSON 对象。
    """
    base = (base_url or "").rstrip("/")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = await _post_json(f"{base}/chat/completions", headers, body, timeout)
    raw = data["choices"][0]["message"]["content"]
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    used_model = data.get("model") or model
    return {"data": parse_structured(raw), "usage": usage, "model": used_model}
