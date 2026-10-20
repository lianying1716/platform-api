"""API key creation must reject limits that can never succeed."""


def test_rate_limit_must_be_positive_and_bounded(client):
    for value in (0, -1, 10001):
        r = client.post(
            "/api/ai/keys",
            json={"name": "bad-limit", "scopes": ["read"], "rate_limit_per_min": value},
        )
        assert r.status_code == 422, (value, r.text)


def test_key_created_with_positive_limit_is_usable(client):
    created = client.post(
        "/api/ai/keys",
        json={"name": "good-limit", "scopes": ["read"], "rate_limit_per_min": 1},
    )
    assert created.status_code == 200, created.text
    raw = created.json()["api_key"]
    headers = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/skills/assets", headers=headers).status_code == 200
    assert client.get("/api/skills/assets", headers=headers).status_code == 429
