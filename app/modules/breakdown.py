"""爆款拆解模块：提示词构造 + LLM 调用 + JSON 解析。

职责（纯模块层，不碰 DB；DB 拼装/落库在 `app/services/breakdown_service.py`）：

- `build_breakdown_prompt(payload) -> (system, user)`：把「元数据 + 转录 + 视频理解(L3) +
  评论摘要 + 后端算好的互动数字」拼成 LLM 的 system/user，纯函数便于单测。
- `run_llm(system, user, *, call_model, timeout) -> dict`：经统一 ``ModelAccessService``
  发起请求，强制 JSON 输出，容错解析严格 JSON；非法输出抛异常（由 worker 落
  `status=error` / 重试，不静默降级）。

红线：
- 数字（相对中位数倍数/互动率/分位）由后端确定性计算后作为**输入**交给模型，模型只做定性，
  禁止重算或杜撰（提示词里已写明）。
- 供应商与模型名来自「模型接入」，由网关注入，不硬编码。

模型请求只经调用方注入的网关回调；本模块不读取供应商配置、不直连 HTTP。
"""

import json
import re
from typing import Callable

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
    call_model: Callable | None = None,
    timeout: float = 90,
) -> dict:
    """经统一模型网关执行拆解，返回 ``{"data", "usage", "model", "routing"}``。

    Args:
        system/user: `build_breakdown_prompt` 产出的提示词。
        call_model: 统一模型网关回调；未注入即视为编程错误。
        timeout: 单次请求超时秒数（拆解为长文本，默认 90s）。

    Raises:
        ValueError: 返回内容不是合法 JSON 对象。
    """
    if call_model is None:
        raise RuntimeError("爆款拆解必须经 ModelAccessService 调用，禁止直连模型接口")
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    result = await call_model(
        request={**body, "timeout": timeout},
        parse_result=parse_structured,
    )
    return {
        "data": result.data if hasattr(result, "data") else result,
        "usage": getattr(result, "usage", {}) or {},
        "model": getattr(result, "used_model_key", ""),
        "routing": result.to_routing() if hasattr(result, "to_routing") else {},
    }
