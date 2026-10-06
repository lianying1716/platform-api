"""爆款拆解配置：提示词 + 爆款判定阈值，存 `system_setting` KV。

红线（同 `comment_settings.py` / `process_settings.py`）：提示词与阈值**不写死在业务代码里**，
有内置默认、可在线改、读取时取最新值。写入侧校验（长度/范围）放 API 层（后续 Task）。

- `breakdown_prompt`：拆解报告的 system 提示词（约束 JSON 结构与「禁编造」）。
- `breakdown_viral_multiple`：对标账号报告里「爆款」判定阈值（播放 >= 该账号中位数 × 倍数）。
"""

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SystemSetting

# system_setting key（单一事实来源；API / 服务复用此映射）。
BREAKDOWN_KEYS: dict[str, str] = {
    "prompt": "breakdown_prompt",
    "viral_multiple": "breakdown_viral_multiple",
}

# 内置默认提示词：定义报告 JSON 结构 + 各分析维度 + 禁编造约束。
# 结构对齐设计 §2.1；数字（data_context/对标）由后端确定性计算，LLM 只做定性。
# 注意：模板含 JSON 字面量花括号，仅供人工编辑，不做 str.format（避免误当占位符）。
DEFAULT_BREAKDOWN_PROMPT = (
    "你是短视频爆款拆解分析师。根据给定的作品元数据、转录文案、视频理解结果、"
    "评论区摘要与后端已算好的互动数字，产出一份「为什么火 + 怎么复刻」的结构化拆解报告。\n"
    "硬性要求：\n"
    "1. 只依据给定材料分析，**禁止编造**任何看不到的细节；材料缺失的字段一律填 null 或空数组。\n"
    "2. 数字（播放/点赞/相对中位数倍数/互动率）已由系统算好，你**只引用、不重算**，更不许杜撰新数字。\n"
    "3. 只输出 JSON，不要解释、不要 Markdown 代码块。\n"
    "输出 JSON 结构：\n"
    "{\n"
    '  "verdict": "一句话结论：这条为什么火",\n'
    '  "score": 0-100 整数（爆款潜力评分）,\n'
    '  "hook": {"first_line": "前3秒台词", "first_frame": "首帧画面", "hook_type": "好奇缺口/反常识/清单体/转变叙事/痛点直呼/其他", "why": "为什么抓人"},\n'
    '  "structure": [{"segment": "0-3s", "role": "钩子/展开/转折/高潮/CTA", "summary": "段落概要"}],\n'
    '  "pacing": {"pattern_interrupts": ["每3-5秒的变化"], "re_engagement_points": [秒数], "emotion_curve": "情绪曲线描述"},\n'
    '  "script": {"reusable_phrases": ["可复用句式"], "money_lines": ["金句/记忆点"]},\n'
    '  "psych_triggers": ["好奇", "认同", "损失厌恶"],\n'
    '  "cta": {"type": "引导方式", "placement": "位置"},\n'
    '  "comment_insights": {"top_themes": ["观众最关心"], "controversies": ["争议点"], "questions": ["可回复/可做下一条的问题"]},\n'
    '  "blueprint": ["复刻步骤 1", "复刻步骤 2"],\n'
    '  "replication_tips": ["给创作者的落地建议"]\n'
    "}"
)

_DEFAULT_VIRAL_MULTIPLE = 3.0


def get_breakdown_settings(db: Session) -> dict:
    """返回拆解配置（短名 -> 值），缺失/非法回退内置默认。

    返回键与 `BREAKDOWN_KEYS` 一致：``{"prompt": str, "viral_multiple": float}``。
    """
    rows = {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}

    raw_prompt = rows.get("breakdown_prompt")
    prompt = raw_prompt if (raw_prompt and raw_prompt.strip()) else DEFAULT_BREAKDOWN_PROMPT

    raw_mult = rows.get("breakdown_viral_multiple")
    try:
        mult = float(str(raw_mult).strip()) if raw_mult not in (None, "") else _DEFAULT_VIRAL_MULTIPLE
    except (TypeError, ValueError):
        mult = _DEFAULT_VIRAL_MULTIPLE
    # 读侧兜底：手改/历史 KV 若为 nan/inf/<=0，回退默认，避免下游（对标报告 JSON 序列化）500。
    if not math.isfinite(mult) or mult <= 0:
        mult = _DEFAULT_VIRAL_MULTIPLE

    return {"prompt": prompt, "viral_multiple": mult}
