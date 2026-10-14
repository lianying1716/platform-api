"""爆款拆解配置：提示词 + 爆款判定阈值，存 `system_setting` KV。

红线（同 `comment_settings.py` / `process_settings.py`）：提示词与阈值**不写死在业务代码里**，
有内置默认、可在线改、读取时取最新值。写入侧校验（长度/范围）放 API 层（后续 Task）。

- `breakdown_prompt`：拆解报告的 system 提示词（约束 JSON 结构与「禁编造」）。
- `breakdown_viral_multiple`：对标账号报告里「爆款」判定阈值（播放 >= 该账号中位数 × 倍数）。
- 自动爆款拆解的「阀门」：播放门槛 / 点赞门槛 / 互动率门槛 / 发布时间窗口 / 命中规则 /
  是否自动转录、自动视频理解、自动拆解。都存 KV，用户可在设置页按需调。
"""

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SystemSetting

# system_setting key（单一事实来源；API / 服务复用此映射）。
BREAKDOWN_KEYS: dict[str, str] = {
    "prompt": "breakdown_prompt",
    "viral_multiple": "breakdown_viral_multiple",
    # 自动爆款拆解的判定阀门
    "min_play": "breakdown_min_play",
    "min_like": "breakdown_min_like",
    "min_engagement_rate": "breakdown_min_engagement_rate",
    "publish_within_days": "breakdown_publish_within_days",
    "match_rule": "breakdown_match_rule",  # any / all
    # 自动执行链：命中阀门的爆款自动转录 / 视频理解 / 拆解
    "auto_transcribe": "breakdown_auto_transcribe",
    "auto_understand": "breakdown_auto_understand",
    "auto_breakdown": "breakdown_auto_breakdown",
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

# 自动爆款判定阀门的默认值。设计取向：**默认只开"相对自身中位数"这一条**（最贴近
# 用户对"爆款"的直觉：比自己平时火），其余阀门默认 0=不限，命中规则默认 any（任一命中）。
# 这样即便用户什么都不调，也不会把所有视频都判成爆款、把加工额度烧光。
_DEFAULTS: dict[str, tuple[object, str]] = {
    "breakdown_min_play": (0, "int"),
    "breakdown_min_like": (0, "int"),
    "breakdown_min_engagement_rate": (0.0, "float"),
    "breakdown_publish_within_days": (0, "int"),
    "breakdown_match_rule": ("any", "str"),
    "breakdown_auto_transcribe": (True, "bool"),
    "breakdown_auto_understand": (False, "bool"),
    "breakdown_auto_breakdown": (False, "bool"),
}

_MATCH_RULES = ("any", "all")


def _coerce(raw, default, kind: str):
    if raw is None or str(raw) == "":
        return default
    try:
        if kind == "int":
            return int(str(raw).strip())
        if kind == "float":
            return float(str(raw).strip())
        if kind == "bool":
            text = str(raw).strip().lower()
            if text in ("1", "true", "yes", "on"):
                return True
            if text in ("0", "false", "no", "off"):
                return False
            return default
        return str(raw)
    except (TypeError, ValueError):
        return default


def resolve_breakdown_settings_from_rows(rows: dict[str, str]) -> dict:
    """从 system_setting 行映射解析拆解配置（不依赖 DB Session）。

    返回键与 ``BREAKDOWN_KEYS`` 一致；所有数值做有限性/非负校验，非法回退默认。
    """
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

    out: dict = {"prompt": prompt, "viral_multiple": mult}
    for key, (default, kind) in _DEFAULTS.items():
        short = key[len("breakdown_"):]  # breakdown_min_play -> min_play
        value = _coerce(rows.get(key), default, kind)
        if kind in ("int", "float"):
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                value = default
        if key == "breakdown_match_rule" and value not in _MATCH_RULES:
            value = default
        out[short] = value
    if out["min_engagement_rate"] > 1:
        out["min_engagement_rate"] = 1.0  # 互动率上限 100%
    return out


def get_breakdown_settings(db: Session) -> dict:
    """返回拆解配置（短名 -> 值），缺失/非法回退内置默认。"""
    rows = {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}
    return resolve_breakdown_settings_from_rows(rows)


def is_viral(data_context: dict, settings_map: dict) -> bool:
    """按配置的阀门判断一条内容是否算「爆款」（纯函数，便于测试）。

    阀门（都是"越大越像爆款"）：
    - ``viral_multiple``：播放 >= 该账号中位数 × 倍数（始终参与；中位数为 0 时不满足）；
    - ``min_play`` / ``min_like``：绝对量门槛（>0 才参与）；
    - ``min_engagement_rate``：(点赞+评论+收藏+分享)/播放 的互动率门槛（>0 才参与）；
    - ``match_rule``：``any``=任一命中即爆款；``all``=全部满足才算。

    返回 False 表示不自动拆解（阀门全部未开启时也返回 False，避免全量误判）。
    """
    checks: list[bool] = []

    mult = float(settings_map.get("viral_multiple") or 0)
    median_play = float(data_context.get("median_play") or 0)
    play = float(data_context.get("play") or 0)
    if mult > 0:
        checks.append(bool(median_play) and play >= median_play * mult)

    min_play = float(settings_map.get("min_play") or 0)
    if min_play > 0:
        checks.append(play >= min_play)

    min_like = float(settings_map.get("min_like") or 0)
    if min_like > 0:
        checks.append(float(data_context.get("like") or 0) >= min_like)

    min_rate = float(settings_map.get("min_engagement_rate") or 0)
    if min_rate > 0:
        checks.append(float(data_context.get("engagement_rate") or 0) >= min_rate)

    if not checks:
        return False
    rule = settings_map.get("match_rule") or "any"
    return all(checks) if rule == "all" else any(checks)
