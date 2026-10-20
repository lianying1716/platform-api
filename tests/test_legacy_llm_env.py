"""Legacy env values are a one-time seed, not a runtime second channel."""

from pathlib import Path

from sqlalchemy import select

from app.models import AiProvider, SystemSetting
from app.services.legacy_llm_migration import claim_legacy_llm_settings


def test_compose_passes_legacy_llm_env_to_api_and_worker():
    text = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()
    # Each key appears once as the container mapping and once in ${...:-}; only
    # the API and worker service env blocks count.
    for key in ("PLATFORM_LLM_BASE_URL", "PLATFORM_LLM_API_KEY", "PLATFORM_LLM_MODEL"):
        assert text.count(f"{key}: ${{{key}:-}}") == 2


def test_env_legacy_claim_is_one_time_and_encrypted(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_base_url", "https://env.example/v1")
    monkeypatch.setattr(settings, "llm_api_key", "sk-env-secret")
    monkeypatch.setattr(settings, "llm_model", "env-model")

    first = claim_legacy_llm_settings(session)
    second = claim_legacy_llm_settings(session)
    assert first.status == "claimed"
    assert second.status == "already"
    provider = session.scalars(select(AiProvider)).one()
    assert "sk-env-secret" not in provider.api_key_encrypted
    assert session.get(SystemSetting, "llm_api_key") is None
