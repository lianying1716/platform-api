"""业务模块经统一网关调用：主备路由、routing 落库与判类 best-effort。"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (
    AiModel,
    AiProvider,
    AnalysisResult,
    ContentItem,
    MediaAsset,
    ModelAssignment,
    Transcript,
    VideoUnderstanding,
    ViralBreakdown,
)
from app.services.model_access_service import ModelCallResult


@pytest.fixture(autouse=True)
def _workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield


def _seed_models(db):
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai", base_url="https://api.example.com/v1",
        api_key_encrypted="cipher", enabled=True, config_revision=1,
    )
    db.add(provider)
    db.flush()
    caps = {
        "text": {"value": True, "source": "probe", "config_revision": 1},
        "json": {"value": True, "source": "probe", "config_revision": 1},
        "image": {"value": True, "source": "probe", "config_revision": 1},
        "video": {
            "value": True, "source": "probe", "config_revision": 1,
            "transport": "openai_video_url",
        },
    }
    primary = AiModel(provider_id=provider.id, model_key="primary", capabilities=caps)
    fallback = AiModel(provider_id=provider.id, model_key="fallback", capabilities=caps)
    db.add_all([primary, fallback])
    db.flush()
    for work_type in ("text_analysis", "cover_classify", "video_understanding"):
        db.add(
            ModelAssignment(
                work_type=work_type,
                primary_model_id=primary.id,
                fallback_model_id=fallback.id,
            )
        )
    db.commit()
    return primary, fallback


def _seed_asset(db, *, with_video=False):
    content = ContentItem(
        account_id=1, platform="douyin", external_id="x1", content_type="video",
        title="标题", author_name="作者", duration_sec=30,
    )
    db.add(content)
    db.flush()
    asset = MediaAsset(
        content_id=content.id, relative_path="douyin/a/1", video_file="v.mp4" if with_video else "",
    )
    db.add(asset)
    db.flush()
    db.add(Transcript(asset_id=asset.id, full_text="转录内容", language="zh", model="base"))
    db.commit()
    return content, asset


def _result(data, *, model="primary", fallback=False):
    return ModelCallResult(
        data=data,
        used_model_id=2 if fallback else 1,
        used_model_key=model,
        used_role="fallback" if fallback else "primary",
        fallback_used=fallback,
        primary_model_id=1,
        primary_model_key="primary",
        fallback_model_id=2,
        fallback_model_key="fallback",
        primary_error_class="timeout" if fallback else "",
        primary_error_message="timeout" if fallback else "",
        fallback_error_class="",
        fallback_error_message="",
        attempt_group_id="g1",
        usage={"total_tokens": 3},
    )


def test_handle_analyze_uses_gateway_and_writes_routing(monkeypatch, session):
    _seed_models(session)
    _content, asset = _seed_asset(session)
    from app.workers import handlers

    async def fake_call(self, db, **kwargs):
        return _result(
            {"summary": "s", "tags": ["t"]},
            model="fallback",
            fallback=True,
        )

    monkeypatch.setattr(
        "app.services.model_access_service.ModelAccessService.call_with_fallback", fake_call
    )
    asyncio.run(handlers.handle_analyze(session, {"asset_id": asset.id}))
    row = session.scalar(select(AnalysisResult).where(AnalysisResult.asset_id == asset.id))
    assert row.summary == "s"
    assert row.model == "fallback"
    assert row.routing["text_analysis"]["fallback_used"] is True


def test_run_understand_routes_both_stages(monkeypatch, session):
    _seed_models(session)
    content, asset = _seed_asset(session, with_video=True)
    Path("douyin/a/1").mkdir(parents=True, exist_ok=True)
    Path("douyin/a/1/v.mp4").write_bytes(b"video")
    from app.services import understand_service as svc

    async def fake_call(self, db, *, work_type, **kwargs):
        if work_type == "cover_classify":
            return _result(
                {"type": "commerce", "confidence": 0.9, "reason": "x", "signals": []}
            )
        return _result(
            {"summary": "视频摘要", "dimensions": {"product": "x"}, "timeline": []}
        )

    monkeypatch.setattr(
        "app.services.model_access_service.ModelAccessService.call_with_fallback", fake_call
    )
    out = asyncio.run(svc.run_understand(session, asset.id))
    assert out.summary == "视频摘要"
    assert out.routing["cover_classify"]["used_model_key"] == "primary"
    assert out.routing["video_understanding"]["used_model_key"] == "primary"
    session.refresh(content)
    assert content.auto_category == "commerce"


def test_run_understand_classify_best_effort(monkeypatch, session):
    _seed_models(session)
    _content, asset = _seed_asset(session, with_video=True)
    Path("douyin/a/1").mkdir(parents=True, exist_ok=True)
    Path("douyin/a/1/v.mp4").write_bytes(b"video")
    from app.services import understand_service as svc
    from app.services.model_access_service import ModelCallError

    async def fake_call(self, db, *, work_type, **kwargs):
        if work_type == "cover_classify":
            err = ModelCallError(
                "classify failed", attempt_group_id="g-classify",
                primary_model_id=1, primary_model_key="primary",
                fallback_model_id=2, fallback_model_key="fallback",
                primary_error_class="timeout", primary_error_message="timeout",
                fallback_error_class="server", fallback_error_message="500",
            )
            raise err
        return _result({"summary": "ok", "dimensions": {}, "timeline": []})

    monkeypatch.setattr(
        "app.services.model_access_service.ModelAccessService.call_with_fallback", fake_call
    )
    out = asyncio.run(svc.run_understand(session, asset.id))
    assert out.status == "done"
    assert out.category == "other"
    stage = out.routing["cover_classify"]
    assert stage["used_model_id"] is None
    assert stage["primary_error_class"] == "timeout"
    assert stage["fallback_error_class"] == "server"


def test_run_breakdown_uses_gateway(monkeypatch, session):
    _seed_models(session)
    _content, asset = _seed_asset(session)
    from app.services import breakdown_service as svc

    captured = {}

    async def fake_call(self, db, *, work_type, parse_result, **kwargs):
        captured["work_type"] = work_type
        captured["prompt"] = kwargs["request"]["messages"][-1]["content"]
        data = {"score": 88, "verdict": "很火", "comment_insights": {}}
        parse_result('{"ignored": true}')
        return _result(data)

    monkeypatch.setattr(
        "app.services.model_access_service.ModelAccessService.call_with_fallback", fake_call
    )
    out = asyncio.run(svc.run_breakdown(session, asset.id, force=True))
    assert out.status == "done"
    assert out.score == 88
    assert captured["work_type"] == "text_analysis"
    assert out.routing["text_analysis"]["used_model_key"] == "primary"
