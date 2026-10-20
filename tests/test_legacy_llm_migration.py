"""旧版 llm_* 配置只允许经一次显式认领进入统一模型网关。"""

import json

from sqlalchemy import select

from app.core.crypto import decrypt_credential
from app.models import AiModel, AiProvider, ModelAssignment, SystemSetting
from app.services.legacy_llm_migration import (
    CLAIM_MARKER,
    claim_legacy_llm_settings,
    legacy_migration_status,
)
from app.services.model_access_service import capabilities_of


def _legacy_rows(session, *, base="https://legacy.example/v1", key="sk-plain-legacy", model="legacy-model"):
    for name, value in {
        "llm_base_url": base,
        "llm_api_key": key,
        "llm_model": model,
        "process_classify_model": "legacy-classify",
        "process_vision_model": "legacy-vision",
        "process_provider": "qwen",
    }.items():
        session.add(SystemSetting(key=name, value=value))
    session.commit()


def test_legacy_claim_is_idempotent_and_encrypts_secret(session):
    _legacy_rows(session)

    first = claim_legacy_llm_settings(session)
    second = claim_legacy_llm_settings(session)

    assert first.status == "claimed"
    assert second.status == "already"
    providers = session.scalars(select(AiProvider)).all()
    assert len(providers) == 1
    provider = providers[0]
    assert provider.base_url == "https://legacy.example/v1"
    assert provider.api_key_encrypted
    assert "sk-plain-legacy" not in provider.api_key_encrypted
    assert decrypt_credential(provider.api_key_encrypted)["api_key"] == "sk-plain-legacy"

    models = session.scalars(select(AiModel).order_by(AiModel.model_key)).all()
    assert {m.model_key for m in models} == {"legacy-classify", "legacy-model", "legacy-vision"}
    assignments = {a.work_type: a for a in session.scalars(select(ModelAssignment)).all()}
    assert set(assignments) == {"text_analysis", "cover_classify", "video_understanding"}
    assert all(a.primary_model_id is not None for a in assignments.values())


def test_legacy_claim_replaces_unreadable_cipher_with_new_secret(session):
    _legacy_rows(session)
    provider = AiProvider(
        name="existing", preset_key="custom", protocol="openai",
        base_url="https://legacy.example/v1", api_key_encrypted="not-a-valid-cipher", config_revision=1,
    )
    session.add(provider)
    session.commit()
    result = claim_legacy_llm_settings(session)
    assert result.status == "claimed"
    session.refresh(provider)
    assert decrypt_credential(provider.api_key_encrypted)["api_key"] == "sk-plain-legacy"


def test_legacy_claim_does_not_invent_missing_slot_models(session):
    session.add_all(
        [
            SystemSetting(key="llm_base_url", value="https://legacy.example/v1"),
            SystemSetting(key="llm_api_key", value="sk-legacy"),
            SystemSetting(key="llm_model", value="legacy-model"),
        ]
    )
    session.commit()
    claim_legacy_llm_settings(session)
    assert {m.model_key for m in session.scalars(select(AiModel)).all()} == {"legacy-model"}
    assert {a.work_type for a in session.scalars(select(ModelAssignment)).all()} == {"text_analysis"}


def test_legacy_claim_never_leaves_plaintext_or_ui_model_selectors(session):
    _legacy_rows(session)
    claim_legacy_llm_settings(session)

    text = "\n".join(row.value for row in session.scalars(select(SystemSetting)).all())
    assert "sk-plain-legacy" not in text
    for key in (
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        "process_provider",
        "process_classify_model",
        "process_vision_model",
    ):
        assert session.get(SystemSetting, key) is None
    marker = json.loads(session.get(SystemSetting, CLAIM_MARKER).value)
    assert marker["provider_id"] == session.scalars(select(AiProvider)).one().id
    assert "api_key" not in marker


def test_legacy_claim_does_not_override_manual_assignment(session):
    _legacy_rows(session)
    provider = AiProvider(
        name="manual", preset_key="custom", protocol="openai",
        base_url="https://manual.example/v1", api_key_encrypted="cipher", config_revision=1,
    )
    session.add(provider)
    session.flush()
    manual = AiModel(provider_id=provider.id, model_key="manual-model", capabilities={})
    session.add(manual)
    session.flush()
    session.add(ModelAssignment(work_type="text_analysis", primary_model_id=manual.id, auto_configured=False))
    session.commit()

    claim_legacy_llm_settings(session)
    assignment = session.scalar(select(ModelAssignment).where(ModelAssignment.work_type == "text_analysis"))
    assert assignment.primary_model_id == manual.id


def test_legacy_claim_repairs_empty_capabilities_from_old_migration(session):
    provider = AiProvider(
        name="0018", preset_key="custom", protocol="openai",
        base_url="https://old.example/v1", api_key_encrypted="cipher", config_revision=1,
    )
    session.add(provider)
    session.flush()
    model = AiModel(provider_id=provider.id, model_key="old-model", capabilities={})
    session.add(model)
    session.flush()
    session.add(ModelAssignment(work_type="text_analysis", primary_model_id=model.id))
    session.commit()

    claim_legacy_llm_settings(session)
    session.refresh(model)
    caps = capabilities_of(model, provider)
    assert caps["text"]["value"] is True
    assert caps["json"]["value"] is True
    assert caps["text"]["source"] == "legacy"


def test_legacy_claim_without_key_keeps_rows_and_reports_actionable_error(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_api_key", "")
    session.add(SystemSetting(key="llm_base_url", value="https://legacy.example/v1"))
    session.add(SystemSetting(key="llm_model", value="legacy-model"))
    session.commit()

    result = claim_legacy_llm_settings(session)
    assert result.status == "needs_api_key"
    assert session.get(SystemSetting, "llm_base_url") is not None
    assert session.scalars(select(AiProvider)).all() == []


def test_missing_key_repair_commit_is_not_lost_on_rollback(session, monkeypatch):
    """Missing-key branch may return after repair; earlier repairs must be durable."""
    from app.config import settings
    from app.services.model_access_service import capabilities_of

    monkeypatch.setattr(settings, "llm_api_key", "")
    provider = AiProvider(
        name="0018", preset_key="custom", protocol="openai",
        base_url="https://old.example/v1", api_key_encrypted="cipher", config_revision=1,
    )
    session.add(provider)
    session.flush()
    model = AiModel(provider_id=provider.id, model_key="old-model", capabilities={})
    session.add(model)
    session.flush()
    session.add(ModelAssignment(work_type="text_analysis", primary_model_id=model.id))
    session.add(SystemSetting(key="llm_base_url", value="https://legacy.example/v1"))
    session.add(SystemSetting(key="llm_model", value="legacy-model"))
    session.commit()

    result = claim_legacy_llm_settings(session)
    assert result.status == "needs_api_key"
    session.expire_all()
    refreshed = session.get(AiModel, model.id)
    assert capabilities_of(refreshed, provider)["text"]["value"] is True


def test_legacy_status_reports_pending_validation(session):
    _legacy_rows(session)
    claim_legacy_llm_settings(session)
    status = legacy_migration_status(session)
    assert status["claimed"] is True
    assert "text_analysis" in status["pending_validation"]
    assert "sk-plain-legacy" not in json.dumps(status)


def test_legacy_status_does_not_report_probed_model_as_pending(session):
    _legacy_rows(session)
    claim_legacy_llm_settings(session)
    assignment = session.scalar(select(ModelAssignment).where(ModelAssignment.work_type == "text_analysis"))
    model = session.get(AiModel, assignment.primary_model_id)
    model.capabilities = {
        "text": {"value": True, "source": "probe", "config_revision": 1, "protocol": "openai"},
        "json": {"value": True, "source": "probe", "config_revision": 1, "protocol": "openai"},
    }
    session.commit()
    assert "text_analysis" not in legacy_migration_status(session)["pending_validation"]
