"""Legacy LLM settings are rejected at the generic settings surface.

The old test-llm endpoint was a second configuration path; provider testing now
lives exclusively under /api/model-access/providers/{id}/test.
"""


def test_system_settings_rejects_llm_api_key(client, session):
    from app.models import SystemSetting

    response = client.put("/api/system/settings", json={"llm_api_key": "sk-plain"})
    assert response.status_code == 422
    assert session.get(SystemSetting, "llm_api_key") is None


def test_system_settings_rejects_legacy_llm_fields(client, session):
    from app.models import SystemSetting

    for payload in (
        {"llm_base_url": "https://api.example.com/v1"},
        {"llm_model": "gpt-x"},
    ):
        response = client.put("/api/system/settings", json=payload)
        assert response.status_code == 422
    assert session.get(SystemSetting, "llm_base_url") is None
    assert session.get(SystemSetting, "llm_model") is None


def test_legacy_test_llm_route_is_gone(client):
    assert client.post("/api/system/settings/test-llm", json={}).status_code in (404, 405)
