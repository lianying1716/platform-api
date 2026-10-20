"""爆款拆解（viral_breakdown）测试。

Task 1：数据模型 + 迁移 0006 —— 表存在、asset_id 唯一。
Task 2：拆解配置 + 提示词模块 —— 默认值/覆盖、prompt 维度与禁编造、run_llm 解析。
后续 Task 3 会在此文件追加服务相关用例。
"""
import asyncio

import pytest

import app.modules.breakdown as b
from sqlalchemy.exc import IntegrityError


def test_viral_breakdown_model_exists(session):
    from sqlalchemy import inspect

    from app.models import ViralBreakdown

    assert ViralBreakdown.__tablename__ == "viral_breakdown"
    tables = set(inspect(session.get_bind()).get_table_names())
    assert "viral_breakdown" in tables


def test_viral_breakdown_columns_shape(_engine):
    """列集合与关键约束。"""
    from sqlalchemy import inspect

    insp = inspect(_engine)
    cols = {c["name"] for c in insp.get_columns("viral_breakdown")}
    assert {
        "id", "asset_id", "score", "verdict", "sections", "comment_insights",
        "data_context", "understanding_available", "model", "prompt_version",
        "usage", "status", "error", "created_at",
    } <= cols

    # asset_id 唯一（unique=True + index=True -> 唯一索引）
    uidx = [i for i in insp.get_indexes("viral_breakdown")
            if i["unique"] and i["column_names"] == ["asset_id"]]
    assert uidx, "viral_breakdown.asset_id 应有唯一索引"


def test_viral_breakdown_unique_asset(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import ContentItem, MediaAsset, ViralBreakdown

    S = sessionmaker(bind=_engine)
    db = S()
    c = ContentItem(account_id=1, platform="douyin", external_id="t1vb", content_type="video")
    db.add(c)
    db.flush()
    asset = MediaAsset(content_id=c.id, video_file="v.mp4")
    db.add(asset)
    db.flush()
    db.add(ViralBreakdown(asset_id=asset.id, verdict="第一条"))
    db.commit()

    # 同一 asset_id 再插一行必须触发唯一约束。
    db.add(ViralBreakdown(asset_id=asset.id, verdict="第二条"))
    try:
        db.commit()
        raise AssertionError("duplicate viral_breakdown.asset_id should fail")
    except IntegrityError:
        db.rollback()
    finally:
        db.close()


# ============================================================
# Task 2：拆解配置 + 提示词模块
# ============================================================


def test_breakdown_settings_defaults(session):
    from app.core.breakdown_settings import DEFAULT_BREAKDOWN_PROMPT, get_breakdown_settings

    s = get_breakdown_settings(session)
    # 基础两项 + 自动爆款阀门的默认键都要在
    assert {"prompt", "viral_multiple"} <= set(s)
    assert {"min_play", "min_like", "min_engagement_rate", "publish_within_days",
            "match_rule", "auto_transcribe", "auto_understand", "auto_breakdown"} <= set(s)
    assert isinstance(s["prompt"], str) and s["prompt"].strip()
    assert s["prompt"] == DEFAULT_BREAKDOWN_PROMPT
    assert isinstance(s["viral_multiple"], float) and s["viral_multiple"] > 1.0
    # 默认不自动拆解（耗资源），命中规则默认任一
    assert s["auto_breakdown"] is False
    assert s["match_rule"] == "any"


def test_breakdown_settings_override(session):
    from app.core.breakdown_settings import get_breakdown_settings
    from app.models import SystemSetting

    session.add(SystemSetting(key="breakdown_prompt", value="自定义提示词"))
    session.add(SystemSetting(key="breakdown_viral_multiple", value="5"))
    session.commit()
    s = get_breakdown_settings(session)
    assert s["prompt"] == "自定义提示词"
    assert s["viral_multiple"] == 5.0


def test_breakdown_settings_bad_value_falls_back(session):
    from app.core.breakdown_settings import DEFAULT_BREAKDOWN_PROMPT, get_breakdown_settings
    from app.models import SystemSetting

    session.add(SystemSetting(key="breakdown_viral_multiple", value="not-a-number"))
    session.add(SystemSetting(key="breakdown_prompt", value=""))
    session.commit()
    s = get_breakdown_settings(session)
    # 非法阈值回退默认；空提示词回退默认（不可用空提示词退化提示质量）。
    assert s["viral_multiple"] == 3.0
    assert s["prompt"] == DEFAULT_BREAKDOWN_PROMPT


def test_build_breakdown_prompt_contains_dimensions_and_guardrails():
    from app.modules.breakdown import build_breakdown_prompt

    payload = {
        "platform": "douyin",
        "title": "这条裙子我回购5次",
        "author": "小红",
        "duration_sec": 32,
        "published_at": "2026-10-01",
        "play": 12000,
        "like": 800,
        "comment": 120,
        "data_context": {"play_vs_median": 4.2, "engagement_rate": 0.076},
        "transcript": "姐妹们这条裙子我回购五次真的不挑人",
        "timeline": [{"t": 1, "event": "展示裙子"}],
        "understanding": {"summary": "一条带货视频", "dimensions": {"product": "裙子"}},
        "comments": ["求链接", "多少钱"],
    }
    system, user = build_breakdown_prompt(payload)

    assert isinstance(system, str) and system.strip()
    assert isinstance(user, str) and user.strip()
    # 报告各维度在提示词中被要求（结论/评分/钩子/结构/节奏/文案/触发/CTA/评论洞察/复刻）。
    for token in (
        "verdict", "score", "hook", "structure", "pacing", "script",
        "psych_triggers", "cta", "comment_insights", "blueprint",
    ):
        assert token in system
    # 禁编造：看不到的字段填 null/空，不许杜撰（红线：数字不让 LLM 算）。
    assert "编造" in system or "杜撰" in system or "不许" in system
    # 输入被带进 user：标题、转录、评论、确定性数字。
    assert "这条裙子我回购5次" in user
    assert "姐妹们这条裙子" in user
    assert "求链接" in user
    assert "4.2" in user  # 相对中位数倍数（后端算好，交给 LLM 只做定性引用）


def test_build_breakdown_prompt_without_understanding_and_comments():
    """降级输入（无 L3、无评论）不抛异常，且如实标注缺失。"""
    from app.modules.breakdown import build_breakdown_prompt

    payload = {"title": "T", "transcript": "abc", "understanding": None, "comments": []}
    system, user = build_breakdown_prompt(payload)
    assert isinstance(system, str) and system
    assert "abc" in user


class _GatewayResult:
    def __init__(self, data, *, model="m", usage=None):
        self.data = data
        self.used_model_key = model
        self.usage = usage or {"total_tokens": 7}

    def to_routing(self):
        return {"used_model_key": self.used_model_key}


def _gateway(content: str, *, capture: dict | None = None, model: str = "m"):
    async def call_model(*, request, parse_result):
        if capture is not None:
            capture.update({"request": request, "timeout": request.get("timeout")})
        data = parse_result(content)
        return _GatewayResult(data, model=model)

    return call_model


def test_run_llm_parses_json():
    cap: dict = {}
    out = asyncio.run(b.run_llm("sys", "usr", call_model=_gateway('{"verdict":"火","score":88}', capture=cap)))
    assert isinstance(out, dict)
    assert out["data"]["verdict"] == "火" and out["data"]["score"] == 88
    assert out["usage"] == {"total_tokens": 7}
    assert out["model"] == "m"
    body = cap["request"]
    assert "model" not in body
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1] == {"role": "user", "content": "usr"}


def test_run_llm_strips_code_fence():
    out = asyncio.run(b.run_llm("s", "u", call_model=_gateway("```json\n{\"verdict\":\"x\"}\n```")))
    assert out["data"]["verdict"] == "x"


def test_run_llm_invalid_json_raises():
    with pytest.raises(ValueError):
        asyncio.run(b.run_llm("s", "u", call_model=_gateway("不是 JSON")))


def test_run_llm_passes_timeout():
    cap: dict = {}
    asyncio.run(b.run_llm("s", "u", call_model=_gateway('{"ok":1}', capture=cap), timeout=42))
    assert cap["timeout"] == 42


def test_run_llm_requires_gateway():
    with pytest.raises(RuntimeError, match="ModelAccessService"):
        asyncio.run(b.run_llm("s", "u"))
