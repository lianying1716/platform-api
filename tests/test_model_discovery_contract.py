"""Provider test/discovery UI contract and shared model-list use case."""

import json

from app.core.crypto import encrypt_credential
from app.models import AiProvider
from app.modules import model_protocols


def _provider(session, *, key="sk-secret"):
    row = AiProvider(
        name="验收供应商",
        preset_key="custom",
        protocol="openai",
        base_url="https://provider.example/v1",
        api_key_encrypted=encrypt_credential({"api_key": key}),
        enabled=True,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_preview_and_discovery_share_one_upstream_call(client, session, monkeypatch):
    provider = _provider(session)
    calls = []

    async def fake_send(request, *, timeout):
        calls.append({"url": request.url, "auth": request.headers, "timeout": timeout})
        return {"data": [{"id": "model-a"}, {"id": "model-b"}]}

    monkeypatch.setattr(model_protocols, "send_request", fake_send)

    preview = client.post(
        "/api/model-access/providers/preview-models",
        json={"protocol": "openai", "base_url": "https://provider.example/v1", "api_key": "sk-secret"},
    )
    discovered = client.post(f"/api/model-access/providers/{provider.id}/discover-models")

    assert preview.status_code == 200, preview.text
    assert discovered.status_code == 200, discovered.text
    assert preview.json()["models"] == ["model-a", "model-b"]
    assert discovered.json()["models"] == ["model-a", "model-b"]
    assert len(calls) == 2
    assert all(call["url"].endswith("/v1/models") for call in calls)


def test_provider_test_persists_success_and_reports_it(client, session, monkeypatch):
    provider = _provider(session)

    async def fake_send(_request, *, timeout):
        return {"data": [{"id": "m1"}]}

    monkeypatch.setattr(model_protocols, "send_request", fake_send)
    response = client.post(f"/api/model-access/providers/{provider.id}/test")
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "message": "连接成功"}

    session.expire_all()
    row = session.get(AiProvider, provider.id)
    assert row.last_test_status == "ok"
    assert row.last_test_error == ""
    assert row.last_test_at is not None
    listed = client.get("/api/model-access/providers").json()
    assert listed[0]["last_test_status"] == "ok"


def test_provider_test_failure_is_persisted_and_secret_is_redacted(client, session, monkeypatch):
    provider = _provider(session, key="sk-should-not-leak")

    async def fake_send(_request, *, timeout):
        raise RuntimeError("provider rejected sk-should-not-leak")

    monkeypatch.setattr(model_protocols, "send_request", fake_send)
    response = client.post(f"/api/model-access/providers/{provider.id}/test")
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False
    assert "sk-should-not-leak" not in json.dumps(response.json())

    session.expire_all()
    row = session.get(AiProvider, provider.id)
    assert row.last_test_status == "error"
    assert "sk-should-not-leak" not in row.last_test_error


def test_discovery_error_is_not_silently_empty(client, session, monkeypatch):
    provider = _provider(session)

    async def fake_send(_request, *, timeout):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(model_protocols, "send_request", fake_send)
    response = client.post(f"/api/model-access/providers/{provider.id}/discover-models")
    assert response.status_code == 502
    assert "无法读取模型清单" in response.json()["detail"]
