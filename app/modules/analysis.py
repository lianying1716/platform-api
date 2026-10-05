"""外部大模型分析封装（P6）。

对转录全文做摘要 / 标签 / 结构化拆解。走 OpenAI 兼容的 chat/completions 接口，
供应商/BaseURL/Key/模型在系统设置里配（settings.llm_*）。未配置时抛出可读错误，
不静默失败。

红线：内容分析首选外部大模型 API；不内置模型。
"""

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger("platform.analysis")

_SYSTEM_PROMPT = (
    "你是短视频选题分析助手。根据给定的标题和转录全文，输出 JSON："
    '{"summary": "100字内摘要", "tags": ["标签"], '
    '"structured": {"hook": "开头钩子", "structure": "内容结构", "selling_point": "核心卖点"}}。'
    "只输出 JSON，不要多余文字。"
)


def _settings_or_error() -> tuple[str, str, str]:
    base = (settings.llm_base_url or "").rstrip("/")
    key = settings.llm_api_key or ""
    model = settings.llm_model or ""
    if not base or not key:
        raise ValueError("未配置大模型 API（在系统设置里填 Base URL 与 API Key）")
    return base, key, model


async def analyze(title: str, full_text: str, prompt: str | None = None) -> dict:
    base, key, model = _settings_or_error()
    user_content = f"标题：{title}\n\n转录全文：\n{full_text[:8000]}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt or _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    raw = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"summary": raw[:200], "tags": [], "structured": {}}
    return {
        "summary": parsed.get("summary", ""),
        "tags": parsed.get("tags", []),
        "structured": parsed.get("structured", {}),
        "model": model,
    }
