"""模型接入端到端：密钥加密入库、主备切换、routing 与审计完整。"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core import crypto
from app.models import AiModel, AiProvider, ModelAssignment, ModelCallAttempt
from app.services.model_access_service import ModelAccessService


def _cfg(db_url: str) -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_model_access_e2e_encrypted_fallback_and_audit(tmp_path, monkeypatch):
    db_url = "sqlite:///" + str(tmp_path / "e2e.db")
    monkeypatch.setattr("app.config.settings.credential_enc_key", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr("app.config.settings.credential_key_file", None)
    crypto._key = None
    command.upgrade(_cfg(db_url), "0018")

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    ciphertext = crypto.encrypt_credential({"api_key": "sk-e2e-secret"})
    provider = AiProvider(
        name="假供应商", preset_key="custom", protocol="openai",
        base_url="https://fake.example/v1", api_key_encrypted=ciphertext,
        enabled=True, config_revision=1,
    )
    db.add(provider)
    db.flush()
    caps = {
        "text": {"value": True, "source": "probe", "config_revision": 1},
        "json": {"value": True, "source": "probe", "config_revision": 1},
    }
    primary = AiModel(provider_id=provider.id, model_key="primary", capabilities=caps)
    fallback = AiModel(provider_id=provider.id, model_key="fallback", capabilities=caps)
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

    calls = {"n": 0}
    seen_auth: list[str] = []

    async def fake_send(request, *, timeout):
        calls["n"] += 1
        seen_auth.append(request.headers.get("Authorization", ""))
        if calls["n"] == 1:
            response = httpx.Response(500, request=httpx.Request("POST", request.url))
            raise httpx.HTTPStatusError("server error", request=response.request, response=response)
        return {"choices": [{"message": {"content": '{"summary":"ok"}'}}], "usage": {"total_tokens": 7}}

    monkeypatch.setattr("app.services.model_access_service.model_protocols.send_request", fake_send)
    result = __import__("asyncio").run(
        ModelAccessService(attempt_session_factory=Session).call_with_fallback(
            db,
            work_type="text_analysis",
            operation="analyze",
            request={"messages": [{"role": "user", "content": "hi"}]},
            parse_result=lambda raw: __import__("json").loads(raw),
        )
    )
    assert result.fallback_used is True
    assert result.used_model_key == "fallback"
    assert seen_auth == ["Bearer sk-e2e-secret", "Bearer sk-e2e-secret"]

    attempts = db.scalars(select(ModelCallAttempt).order_by(ModelCallAttempt.id)).all()
    assert [row.status for row in attempts] == ["error", "ok"]
    assert attempts[0].error_class == "server"
    assert "sk-e2e-secret" not in (attempts[0].error_message + str(attempts[0].request_meta))
    assert all("sk-e2e-secret" not in str(row.__dict__) for row in attempts)
    db.close()
    engine.dispose()
    crypto._key = None
