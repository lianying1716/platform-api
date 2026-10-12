"""模型网关：主备切换、能力预检、解析失败和独立审计 session。"""

import asyncio

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import AiModel, AiProvider, ModelAssignment, ModelCallAttempt
from app.services.model_access_service import ModelAccessService, ModelCallError


def _engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


def _seed(engine, *, primary_enabled=True, fallback_enabled=True):
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai", base_url="https://api.example.com/v1",
        api_key_encrypted="cipher", config_revision=1,
    )
    db.add(provider)
    db.flush()
    caps = {
        "text": {"value": True, "source": "probe", "config_revision": 1},
        "json": {"value": True, "source": "probe", "config_revision": 1},
        "image": {"value": True, "source": "probe", "config_revision": 1},
        "video": {
            "value": True,
            "source": "probe",
            "config_revision": 1,
            "transport": "openai_video_url",
        },
    }
    primary = AiModel(
        provider_id=provider.id, model_key="primary", display_name="primary",
        enabled=primary_enabled, capabilities=caps,
    )
    fallback = AiModel(
        provider_id=provider.id, model_key="fallback", display_name="fallback",
        enabled=fallback_enabled, capabilities=caps,
    )
    db.add_all([primary, fallback])
    db.flush()
    db.add(
        ModelAssignment(
            work_type="text_analysis",
            primary_model_id=primary.id,
            fallback_model_id=fallback.id,
        )
    )
    db.commit()
    return Session, db, provider, primary, fallback


def _ok_request(monkeypatch, *, primary_errors=0, fallback_errors=0, parsed=None):
    calls = {"n": 0}

    async def fake_send(request, *, timeout):
        calls["n"] += 1
        current = calls["n"]
        if current <= primary_errors:
            raise httpx.ConnectTimeout("primary timeout")
        if current <= primary_errors + fallback_errors:
            raise httpx.ConnectTimeout("fallback timeout")
        return {"choices": [{"message": {"content": parsed or '{"ok": true}'}}]}

    monkeypatch.setattr("app.services.model_access_service.model_protocols.send_request", fake_send)
    monkeypatch.setattr("app.services.model_access_service.decrypt_credential", lambda _c: {"api_key": "secret"})
    return calls


def test_primary_success_writes_attempt(monkeypatch):
    engine = _engine()
    Session, db, _p, primary, _f = _seed(engine)
    calls = _ok_request(monkeypatch)
    svc = ModelAccessService(attempt_session_factory=Session)
    result = asyncio.run(
        svc.call_with_fallback(
            db,
            work_type="text_analysis",
            operation="analyze",
            request={"messages": [{"role": "user", "content": "hi"}]},
            parse_result=lambda raw: {"data": raw},
        )
    )
    assert result.used_model_id == primary.id
    assert result.fallback_used is False
    assert calls["n"] == 1
    rows = db.scalars(select(ModelCallAttempt)).all()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].finished_at is not None


def test_primary_failure_falls_back_and_keeps_both(monkeypatch):
    engine = _engine()
    Session, db, _p, _primary, fallback = _seed(engine)
    _ok_request(monkeypatch, primary_errors=1)
    svc = ModelAccessService(attempt_session_factory=Session)
    result = asyncio.run(
        svc.call_with_fallback(
            db,
            work_type="text_analysis",
            operation="analyze",
            request={"messages": [{"role": "user", "content": "hi"}]},
            parse_result=lambda raw: {"data": raw},
        )
    )
    assert result.used_model_id == fallback.id
    assert result.fallback_used is True
    assert result.primary_error_class == "timeout"
    rows = db.scalars(select(ModelCallAttempt).order_by(ModelCallAttempt.id)).all()
    assert [r.status for r in rows] == ["error", "ok"]
    assert rows[0].error_class == "timeout"
    assert rows[0].finished_at is not None


def test_both_fail_raises_structured_error(monkeypatch):
    engine = _engine()
    Session, db, _p, primary, fallback = _seed(engine)
    _ok_request(monkeypatch, primary_errors=1, fallback_errors=1)
    svc = ModelAccessService(attempt_session_factory=Session)
    with pytest.raises(ModelCallError) as ei:
        asyncio.run(
            svc.call_with_fallback(
                db,
                work_type="text_analysis",
                operation="analyze",
                request={"messages": [{"role": "user", "content": "hi"}]},
                parse_result=lambda raw: {"data": raw},
            )
        )
    err = ei.value
    assert err.primary_model_id == primary.id
    assert err.fallback_model_id == fallback.id
    assert err.primary_error_class == "timeout"
    assert err.fallback_error_class == "timeout"
    assert err.to_routing()["used_model_id"] is None
    rows = db.scalars(select(ModelCallAttempt)).all()
    assert len(rows) == 2
    assert all(r.status == "error" for r in rows)


def test_parse_failure_switches_to_fallback(monkeypatch):
    engine = _engine()
    Session, db, _p, primary, fallback = _seed(engine)

    calls = {"n": 0}

    async def fake_send(request, *, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {"content": "not-json"}}]}
        return {"choices": [{"message": {"content": "good"}}]}

    monkeypatch.setattr("app.services.model_access_service.model_protocols.send_request", fake_send)
    monkeypatch.setattr("app.services.model_access_service.decrypt_credential", lambda _c: {"api_key": "secret"})

    def parse(raw):
        if raw == "not-json":
            raise ValueError("bad json")
        return {"ok": raw}

    svc = ModelAccessService(attempt_session_factory=Session)
    result = asyncio.run(
        svc.call_with_fallback(
            db,
            work_type="text_analysis",
            operation="analyze",
            request={"messages": [{"role": "user", "content": "hi"}]},
            parse_result=parse,
        )
    )
    assert result.used_model_id == fallback.id
    assert result.primary_error_class == "unknown"


def test_disabled_primary_uses_fallback_without_attempt(monkeypatch):
    engine = _engine()
    Session, db, _p, primary, fallback = _seed(engine, primary_enabled=False)
    _ok_request(monkeypatch)
    svc = ModelAccessService(attempt_session_factory=Session)
    result = asyncio.run(
        svc.call_with_fallback(
            db,
            work_type="text_analysis",
            operation="analyze",
            request={"messages": [{"role": "user", "content": "hi"}]},
            parse_result=lambda raw: {"data": raw},
        )
    )
    assert result.used_model_id == fallback.id
    assert result.primary_error_class == "disabled"
    rows = db.scalars(select(ModelCallAttempt)).all()
    assert len(rows) == 1 and rows[0].model_id == fallback.id


def test_missing_assignment_preflight_raises(monkeypatch):
    engine = _engine()
    Session, db, _p, _primary, _fallback = _seed(engine)
    assignment = db.scalars(select(ModelAssignment)).all()[0]
    assignment.primary_model_id = None
    assignment.fallback_model_id = None
    db.commit()
    svc = ModelAccessService(attempt_session_factory=Session)
    with pytest.raises(ModelCallError) as ei:
        asyncio.run(
            svc.call_with_fallback(
                db,
                work_type="text_analysis",
                operation="analyze",
                request={"messages": [{"role": "user", "content": "hi"}]},
                parse_result=lambda raw: raw,
            )
        )
    assert ei.value.primary_error_class == "not_configured"
    assert db.scalars(select(ModelCallAttempt)).all() == []
