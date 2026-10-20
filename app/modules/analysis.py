"""外部大模型分析封装（P6）：对转录全文做「速览」——摘要 + 标签。

职责边界（与爆款拆解区分）：
- 本模块只产出*轻量速览*：``summary``（100 字内）+ ``tags``。转录完可自动接着跑，
  速度快、成本低，用于详情页扫一眼和 Markdown/JSON 导出。
- 「为什么火 + 怎么复刻」的深度报告属于爆款拆解（``app/modules/breakdown.py``），
  由用户手动触发。两者不重复：这里不做钩子/结构/卖点的结构化拆解。

模型请求由调用方注入的 ``ModelAccessService`` 回调发起；本模块不再读取
``llm_*``，也不自行选择供应商或模型。

红线：内容分析首选外部大模型 API；不内置模型。
"""

import json
import logging
from typing import Callable

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
    call_model: Callable,
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
