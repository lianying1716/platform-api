"""模型接入 API：双轨鉴权、脱敏、导入、分配与审计查询。"""

import json

from sqlalchemy import select

from app.core.api_auth import generate_key
from app.models import AiModel, AiProvider, ApiKey, ModelAssignment, ModelCallAttempt, SystemSetting


def _api_key(session, scopes=("admin",), *, raw: str | None = None) -> str:
    if raw is None:
        raw, key_hash, prefix = generate_key()
    else:
        import hashlib

        key_hash, prefix = hashlib.sha256(raw.encode()).hexdigest(), raw[:10]
    session.add(
        ApiKey(
            name="test",
            key_hash=key_hash,
            prefix=prefix,
            scopes=list(scopes),
            rate_limit_per_min=1000,
        )
    )
    session.commit()
    return raw


def test_create_provider_encrypts_and_redacts(client, session):
    resp = client.post(
        "/api/model-access/providers",
        json={
            "name": "我的 OpenAI",
            "preset_key": "openai",
            "protocol": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-secret-value",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_api_key"] is True
    assert "secret" not in json.dumps(body)
    row = session.scalar(select(AiProvider))
    assert row is not None and row.api_key_encrypted and "sk-secret-value" not in row.api_key_encrypted


def test_legacy_status_is_visible_without_secret_values(client, session):
    session.add(SystemSetting(key="llm_base_url", value="https://legacy.example/v1"))
    session.add(SystemSetting(key="llm_model", value="legacy-model"))
    session.commit()

    response = client.get("/api/model-access/legacy")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected"] is True
    assert "legacy-model" not in json.dumps(body)
    assert "sk-" not in json.dumps(body)


def test_import_models_and_list_not_expose_ciphertext(client, session):
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai", base_url="https://api.example.com/v1",
        api_key_encrypted="cipher", enabled=True,
    )
    session.add(provider)
    session.commit()
    resp = client.post(
        "/api/model-access/models/import",
        json={
            "provider_id": provider.id,
            "models": [{"model_key": "m1", "display_name": "M1"}],
            "probe": {"text": False, "image": False, "video": False},
        },
    )
    assert resp.status_code == 200, resp.text
    listed = client.get("/api/model-access/models")
    assert listed.status_code == 200
    text = json.dumps(listed.json())
    assert "cipher" not in text
    assert listed.json()[0]["model_key"] == "m1"


def test_setup_state_tracks_generic_provider_model_progression(client, session):
    assert client.get("/api/model-access/assignments").json()["setup_state"]["stage"] == "no_provider"

    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai", base_url="https://api.example.com/v1",
        api_key_encrypted="cipher", enabled=False,
    )
    session.add(provider)
    session.commit()
    assert client.get("/api/model-access/assignments").json()["setup_state"]["stage"] == "provider_disabled"

    provider.enabled = True
    session.commit()
    assert client.get("/api/model-access/assignments").json()["setup_state"]["stage"] == "no_models"

    model = AiModel(
        provider_id=provider.id,
        model_key="m1",
        capabilities={
            "text": {"value": True, "source": "probe", "config_revision": 1},
            "json": {"value": True, "source": "probe", "config_revision": 1},
        },
    )
    session.add(model)
    session.commit()
    state = client.get("/api/model-access/assignments").json()["setup_state"]
    assert state["stage"] == "no_assignment"

    client.put(
        "/api/model-access/assignments/text_analysis",
        json={"primary_model_id": model.id, "fallback_model_id": None},
    )
    state = client.get("/api/model-access/assignments").json()["setup_state"]
    assert state["stage"] == "ready"
    assert "cover_classify" in state["missing_work_types"]


def test_assignment_validation_and_update(client, session):
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai", base_url="https://api.example.com/v1",
        api_key_encrypted="cipher", enabled=True, config_revision=1,
    )
    session.add(provider)
    session.flush()
    caps = {
        "text": {"value": True, "source": "probe", "config_revision": 1},
        "json": {"value": True, "source": "probe", "config_revision": 1},
    }
    model = AiModel(provider_id=provider.id, model_key="m1", capabilities=caps)
    session.add(model)
    session.commit()
    resp = client.put(
        "/api/model-access/assignments/text_analysis",
        json={"primary_model_id": model.id, "fallback_model_id": None},
    )
    assert resp.status_code == 200, resp.text
    assignment = session.scalar(
        select(ModelAssignment).where(ModelAssignment.work_type == "text_analysis")
    )
    assert assignment.primary_model_id == model.id
    assert assignment.auto_configured is False

    bad = client.put(
        "/api/model-access/assignments/text_analysis",
        json={"primary_model_id": model.id, "fallback_model_id": model.id},
    )
    assert bad.status_code == 422


def test_read_scope_can_read_but_not_create(anon_client, session):
    raw = _api_key(session, scopes=("read",))
    headers = {"Authorization": f"Bearer {raw}"}
    assert anon_client.get("/api/model-access/models", headers=headers).status_code == 200
    denied = anon_client.post(
        "/api/model-access/providers",
        headers=headers,
        json={"name": "x", "protocol": "openai", "base_url": "https://x/v1", "api_key": "k"},
    )
    assert denied.status_code == 403


def test_operate_cannot_change_provider_connection(anon_client, session):
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai", base_url="https://old.example.com/v1",
        api_key_encrypted="cipher",
    )
    session.add(provider)
    session.commit()
    raw = _api_key(session, scopes=("operate",))
    headers = {"Authorization": f"Bearer {raw}"}
    resp = anon_client.patch(
        f"/api/model-access/providers/{provider.id}",
        headers=headers,
        json={"base_url": "https://evil.example.com/v1"},
    )
    assert resp.status_code == 403


def test_provider_test_and_discovery_handle_undecryptable_key(client, session):
    provider = AiProvider(
        name="broken", preset_key="custom", protocol="openai",
        base_url="https://api.example.com/v1", api_key_encrypted="not-a-valid-ciphertext",
    )
    session.add(provider)
    session.commit()

    tested = client.post(f"/api/model-access/providers/{provider.id}/test")
    assert tested.status_code == 200
    assert tested.json()["ok"] is False
    assert "not-a-valid" not in json.dumps(tested.json())

    discovered = client.post(f"/api/model-access/providers/{provider.id}/discover-models")
    assert discovered.status_code == 502
    assert "无法读取模型清单" in discovered.json()["detail"]


def test_provider_connection_change_keeps_capabilities_visible_as_stale(client, session):
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai", base_url="https://old.example.com/v1",
        api_key_encrypted="cipher", enabled=True, config_revision=1,
    )
    session.add(provider)
    session.flush()
    session.add(
        AiModel(
            provider_id=provider.id,
            model_key="m1",
            capabilities={
                "text": {"value": True, "source": "probe", "config_revision": 1},
                "json": {"value": True, "source": "probe", "config_revision": 1},
            },
            probe_status="passed",
        )
    )
    session.commit()

    resp = client.patch(
        f"/api/model-access/providers/{provider.id}",
        json={"base_url": "https://new.example.com/v1"},
    )
    assert resp.status_code == 200, resp.text

    models = client.get("/api/model-access/models").json()
    assert models[0]["probe_status"] == "untested"
    assert models[0]["capabilities"]["text"]["source"] == "stale"
    assert models[0]["capabilities"]["text"]["value"] is False
    assert models[0]["capabilities"]["text"]["detail"] == "供应商连接配置已变更，需重新检测"


def test_api_key_change_marks_capabilities_stale(client, session):
    from app.core.crypto import encrypt_credential

    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai", base_url="https://api.example.com/v1",
        api_key_encrypted=encrypt_credential({"api_key": "old-key"}),
        enabled=True, config_revision=1,
    )
    session.add(provider)
    session.flush()
    session.add(AiModel(
        provider_id=provider.id,
        model_key="m1",
        capabilities={"text": {"value": True, "source": "probe", "config_revision": 1}},
        probe_status="passed",
    ))
    session.commit()

    response = client.patch(
        f"/api/model-access/providers/{provider.id}", json={"api_key": "new-key"}
    )
    assert response.status_code == 200, response.text
    session.refresh(provider)
    assert provider.config_revision == 2
    models = client.get("/api/model-access/models").json()
    assert models[0]["capabilities"]["text"]["source"] == "stale"


def test_provider_connection_change_only_clears_its_own_pending_flag(client, session):
    changed = AiProvider(
        name="changed", preset_key="custom", protocol="openai",
        base_url="https://changed.example/v1", api_key_encrypted="cipher", config_revision=1,
    )
    other = AiProvider(
        name="other", preset_key="custom", protocol="openai",
        base_url="https://other.example/v1", api_key_encrypted="cipher", config_revision=1,
    )
    session.add_all([changed, other])
    session.flush()
    changed_model = AiModel(provider_id=changed.id, model_key="changed-model", capabilities={})
    other_model = AiModel(provider_id=other.id, model_key="other-model", capabilities={})
    session.add_all([changed_model, other_model])
    session.flush()
    session.add_all([
        ModelAssignment(work_type="text_analysis", primary_model_id=changed_model.id, legacy_pending_validation=True),
        ModelAssignment(work_type="cover_classify", primary_model_id=other_model.id, legacy_pending_validation=True),
    ])
    session.commit()

    response = client.patch(f"/api/model-access/providers/{changed.id}", json={"base_url": "https://changed2.example/v1"})
    assert response.status_code == 200, response.text
    session.expire_all()
    changed_flag = session.scalar(select(ModelAssignment).where(ModelAssignment.work_type == "text_analysis"))
    other_flag = session.scalar(select(ModelAssignment).where(ModelAssignment.work_type == "cover_classify"))
    # Connection changes never assert "validated": the pending warning must stay
    # until a real probe succeeds or the user manually marks the capability.
    assert changed_flag.legacy_pending_validation is True
    assert other_flag.legacy_pending_validation is True


def test_api_key_channel_ignores_admin_cookie(anon_client, session):
    raw = _api_key(session, scopes=("read",))
    anon_client.cookies.set("xsai_session", "garbage")
    resp = anon_client.get(
        "/api/model-access/models", headers={"Authorization": f"Bearer {raw}"}
    )
    assert resp.status_code == 200


def test_calls_query_returns_full_error(client, session):
    session.add(
        ModelCallAttempt(
            attempt_group_id="g1", work_type="text_analysis", operation="analyze", role="primary",
            provider_name="p", model_key="m", status="error", error_class="server",
            error_message="HTTP 500: boom",
        )
    )
    session.commit()
    resp = client.get("/api/model-access/calls/g1")
    assert resp.status_code == 200
    assert resp.json()[0]["error_message"] == "HTTP 500: boom"


def test_calls_list_returns_total_header_filters_and_asset_title(client, session):
    from app.models import ContentItem, MediaAsset

    content = ContentItem(account_id=1, platform="bilibili", external_id="x", title="测试素材")
    session.add(content)
    session.flush()
    asset = MediaAsset(content_id=content.id)
    session.add(asset)
    session.flush()
    session.add_all(
        [
            ModelCallAttempt(
                attempt_group_id="g1", work_type="text_analysis", operation="analyze", role="primary",
                provider_name="p", model_key="m", status="ok", asset_id=asset.id, duration_ms=12,
            ),
            ModelCallAttempt(
                attempt_group_id="g2", work_type="video_understanding", operation="understand_video",
                role="primary", provider_name="p", model_key="m", status="error", error_class="timeout",
                error_message="读取超过 90 秒",
            ),
        ]
    )
    session.commit()

    resp = client.get("/api/model-access/calls?work_type=text_analysis&limit=10")
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "1"
    body = resp.json()
    assert len(body) == 1
    assert body[0]["asset_title"] == "测试素材"
    assert body[0]["duration_ms"] == 12


def test_calls_list_redacts_legacy_error_and_request_meta_on_read(client, session):
    session.add(
        ModelCallAttempt(
            attempt_group_id="g1", work_type="text_analysis", operation="analyze", role="primary",
            provider_name="p", model_key="m", status="error",
            error_message="upstream echoed api_key=legacy-secret-123",
            request_meta={"authorization": "Bearer legacy-secret-123", "protocol": "openai"},
        )
    )
    session.commit()

    row = client.get("/api/model-access/calls").json()[0]
    assert "legacy-secret-123" not in row["error_message"]
    assert "legacy-secret-123" not in json.dumps(row["request_meta"])


def test_model_payload_exposes_effective_protocol(client, session):
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai",
        base_url="https://api.example.com/v1", api_key_encrypted="cipher",
    )
    session.add(provider)
    session.flush()
    model = AiModel(provider_id=provider.id, model_key="m", capabilities={})
    session.add(model)
    session.commit()

    row = client.get("/api/model-access/models").json()[0]
    assert row["protocol"] == "openai"          # 继承供应商
    assert row["protocol_source"] == "inherit"
    assert row["protocol_override"] is None
    assert row["provider_protocol"] == "openai"


def test_patch_model_protocol_sets_manual_and_frees_inherit(client, session):
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai",
        base_url="https://api.example.com/v1", api_key_encrypted="cipher",
    )
    session.add(provider)
    session.flush()
    model = AiModel(provider_id=provider.id, model_key="m", capabilities={})
    session.add(model)
    session.commit()

    patched = client.patch(f"/api/model-access/models/{model.id}", json={"protocol": "gemini"})
    assert patched.status_code == 200
    assert patched.json()["protocol"] == "gemini"
    assert patched.json()["protocol_source"] == "manual"

    restored = client.patch(f"/api/model-access/models/{model.id}", json={"protocol_source": "inherit"})
    assert restored.status_code == 200
    assert restored.json()["protocol"] == "openai"       # 回到继承
    assert restored.json()["protocol_override"] is None


def test_patch_model_protocol_rejects_unknown(client, session):
    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai",
        base_url="https://api.example.com/v1", api_key_encrypted="cipher",
    )
    session.add(provider)
    session.flush()
    model = AiModel(provider_id=provider.id, model_key="m", capabilities={})
    session.add(model)
    session.commit()

    bad = client.patch(f"/api/model-access/models/{model.id}", json={"protocol": "bogus"})
    assert bad.status_code == 422


def test_changing_model_protocol_marks_capabilities_stale(client, session):
    from app.services.model_access_service import capabilities_of

    provider = AiProvider(
        name="p", preset_key="custom", protocol="openai",
        base_url="https://api.example.com/v1", api_key_encrypted="cipher",
        enabled=True, config_revision=1,
    )
    session.add(provider)
    session.flush()
    model = AiModel(
        provider_id=provider.id, model_key="m",
        capabilities={
            "text": {"value": True, "source": "probe", "config_revision": 1, "protocol": "openai"},
        },
    )
    session.add(model)
    session.commit()

    # 同一协议下能力有效
    assert capabilities_of(model, provider)["text"]["value"] is True

    resp = client.patch(f"/api/model-access/models/{model.id}", json={"protocol": "gemini"})
    assert resp.status_code == 200
    session.refresh(model)
    caps = capabilities_of(model, provider)
    assert caps["text"]["value"] is False
    assert caps["text"]["source"] == "stale"
    assert "协议" in caps["text"]["detail"]
