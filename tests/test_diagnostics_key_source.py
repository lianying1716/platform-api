"""Credential diagnostics must use the real key resolver."""


def test_diagnostics_uses_key_is_configured_and_reports_source(client, monkeypatch):
    monkeypatch.setattr("app.core.crypto.key_is_configured", lambda: True)
    response = client.get("/api/system/diagnostics")
    assert response.status_code == 200
    row = next(c for c in response.json()["checks"] if c["name"] == "加密密钥")
    assert row["ok"] is True
    assert "来源" in row["detail"]
    assert "环境变量" in row["detail"] or "secret.key" in row["detail"]


def test_diagnostics_reports_missing_key_without_creating_fallback(client, monkeypatch):
    monkeypatch.setattr("app.core.crypto.key_is_configured", lambda: False)
    response = client.get("/api/system/diagnostics")
    row = next(c for c in response.json()["checks"] if c["name"] == "加密密钥")
    assert row["ok"] is False
    assert "未配置" in row["detail"]
