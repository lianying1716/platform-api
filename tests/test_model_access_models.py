"""模型接入四张表与结果表新增字段的模型级测试。"""

from sqlalchemy import inspect

from app.models import (
    AiModel,
    AiProvider,
    AnalysisResult,
    ModelAssignment,
    ModelCallAttempt,
    VideoUnderstanding,
    ViralBreakdown,
)


def test_model_access_tables_exist(session):
    tables = set(inspect(session.get_bind()).get_table_names())
    assert {"ai_provider", "ai_model", "model_assignment", "model_call_attempt"} <= tables


def test_result_tables_have_routing_and_wide_model(session):
    inspector = inspect(session.get_bind())
    for table in ("analysis_result", "video_understanding", "viral_breakdown"):
        columns = {col["name"]: col for col in inspector.get_columns(table)}
        assert "routing" in columns
        assert columns["model"]["type"].length == 200


def test_provider_model_defaults(session):
    p = AiProvider(
        name="我的 OpenAI",
        preset_key="openai",
        protocol="openai",
        base_url="https://api.openai.com/v1",
        api_key_encrypted="cipher",
    )
    session.add(p)
    session.flush()

    m = AiModel(provider_id=p.id, model_key="gpt-5.4-mini", display_name="GPT 5.4 Mini")
    session.add(m)
    session.commit()

    assert p.config_revision == 1
    assert p.credential_source == "api_key"
    assert p.enabled is True
    assert m.capabilities == {}
    assert m.probe_status == "untested"
    assert m.import_source == "discovered"
    assert m.enabled is True


def test_assignment_unique_work_type(session):
    p = AiProvider(name="p", preset_key="custom", protocol="openai", base_url="http://x")
    session.add(p)
    session.flush()
    m = AiModel(provider_id=p.id, model_key="m")
    session.add(m)
    session.flush()
    session.add(ModelAssignment(work_type="text_analysis", primary_model_id=m.id))
    session.commit()

    assert session.query(ModelAssignment).one().work_type == "text_analysis"


def test_attempt_lifecycle_fields(session):
    row = ModelCallAttempt(
        attempt_group_id="g1",
        work_type="text_analysis",
        operation="analyze",
        role="primary",
        provider_name="p",
        model_key="m",
        status="running",
    )
    session.add(row)
    session.commit()

    assert row.finished_at is None
    assert row.usage == {}
    assert row.request_meta == {}


def test_result_routing_roundtrip(session):
    from app.models import ContentItem, MediaAsset

    c = ContentItem(account_id=1, platform="douyin", external_id="x1", content_type="video")
    session.add(c)
    session.flush()
    a = MediaAsset(content_id=c.id)
    session.add(a)
    session.flush()

    ar = AnalysisResult(asset_id=a.id, model="m", routing={"text_analysis": {"fallback_used": False}})
    vu = VideoUnderstanding(asset_id=a.id, routing={"cover_classify": {"fallback_used": False}})
    vb = ViralBreakdown(asset_id=a.id, routing={"text_analysis": {"fallback_used": True}})
    session.add_all([ar, vu, vb])
    session.commit()

    assert ar.routing["text_analysis"]["fallback_used"] is False
    assert vu.routing["cover_classify"]["fallback_used"] is False
    assert vb.routing["text_analysis"]["fallback_used"] is True

