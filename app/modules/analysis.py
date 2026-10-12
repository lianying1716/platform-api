"""外部大模型分析封装（P6）：对转录全文做「速览」——摘要 + 标签。

职责边界（与爆款拆解区分）：
- 本模块只产出*轻量速览*：``summary``（100 字内）+ ``tags``。转录完可自动接着跑，
  速度快、成本低，用于详情页扫一眼和 Markdown/JSON 导出。
- 「为什么火 + 怎么复刻」的深度报告属于爆款拆解（``app/modules/breakdown.py``），
  由用户手动触发。两者不重复：这里不做钩子/结构/卖点的结构化拆解。

走 OpenAI 兼容的 chat/completions 接口，供应商/BaseURL/Key/模型由调用方通过
统一模型网关注入；未配置时抛出可读错误，不静默失败。

红线：内容分析首选外部大模型 API；不内置模型。
"""

import json
import logging
from typing import Callable

import httpx

from app.config import settings

logger = logging.getLogger("platform.analysis")

_SYSTEM_PROMPT = (
    "你是短视频内容速览助手。根据给定的标题和转录全文，输出 JSON："
    '{"summary": "100字内摘要", "tags": ["标签"]}。'
    "只输出 JSON，不要多余文字。"
)


async def analyze(
    title: str,
    full_text: str,
    prompt: str | None = None,
    *,
    call_model: Callable | None = None,
) -> dict:
    """构造分析请求；``call_model`` 由服务层注入统一网关。"""
    user_content = f"标题：{title}\n\n转录全文：\n{full_text[:8000]}"
    request = {
        "messages": [
            {"role": "system", "content": prompt or _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    if call_model is None:
        # 兼容期：迁移尚未接管或旧 API 直调时仍走 llm_* 配置；生产 handler 一律注入网关。
        base = (settings.llm_base_url or "").rstrip("/")
        key = settings.llm_api_key or ""
        model = settings.llm_model or ""
        if not base or not key:
            raise ValueError("未配置大模型 API（在系统设置里填 Base URL 与 API Key）")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=request,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
        return _parse_analysis(raw, model)

    result = await call_model(request=request, parse_result=_parse_analysis)
    data = result.data if hasattr(result, "data") else result
    if not isinstance(data, dict):
        raise ValueError("分析返回不是 JSON 对象")
    return {
        **data,
        "model": getattr(result, "used_model_key", ""),
        "routing": result.to_routing() if hasattr(result, "to_routing") else {},
    }


def _parse_analysis(raw, model: str = "") -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"summary": str(raw)[:200], "tags": []}
    return {
        "summary": parsed.get("summary", ""),
        "tags": parsed.get("tags", []),
        "model": model,
    }
