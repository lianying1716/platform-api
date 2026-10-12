"""凭证加密往返与脱敏测试。密钥走环境变量，避免落盘。"""

import base64
import os

import pytest


@pytest.fixture(autouse=True)
def _env_key(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PLATFORM_CREDENTIAL_ENC_KEY", key)
    # 重置已加载的模块级密钥缓存
    import app.config as config

    config.settings.credential_enc_key = key
    import app.core.crypto as crypto

    crypto._key = None
    yield
    crypto._key = None


def test_encrypt_decrypt_roundtrip():
    from app.core.crypto import decrypt_credential, encrypt_credential

    payload = {"sessdata": "abc123", "bili_jct": "tok", "中文": "值"}
    token = encrypt_credential(payload)
    assert token != str(payload)
    assert decrypt_credential(token) == payload


def test_each_encrypt_uses_fresh_nonce():
    from app.core.crypto import encrypt_credential

    p = {"k": "v"}
    assert encrypt_credential(p) != encrypt_credential(p)


def test_mask():
    from app.core.crypto import mask

    assert mask("") == ""
    assert mask("short") == "*" * 5
    assert mask("abcdefghij") == "abcd****ghij"


def test_redact_text_masks_configured_secret_and_auth_fields():
    from app.core.crypto import redact_text

    secret = "custom-secret-without-known-prefix"
    text = (
        f"upstream failed key={secret} Authorization: Bearer {secret} "
        "api_key=another-secret x-api-key=third-secret AIza" + "a" * 30
    )
    out = redact_text(text, extra_secrets=(secret,))
    assert secret not in out
    assert "another-secret" not in out
    assert "third-secret" not in out
    assert "AIza" + "a" * 30 not in out
    assert "[REDACTED]" in out


def test_redact_text_masks_json_auth_fields():
    import json

    from app.core.crypto import redact_text

    text = json.dumps(
        {
            "authorization": "Bearer legacy-secret-123",
            "x-api-key": "anthropic-secret",
            "api_key": "plain-secret",
            "protocol": "openai",
        }
    )
    out = redact_text(text)
    assert "legacy-secret-123" not in out
    assert "anthropic-secret" not in out
    assert "plain-secret" not in out
    assert '"protocol": "openai"' in out


def test_bad_key_length_rejected(monkeypatch):
    import app.config as config
    import app.core.crypto as crypto

    config.settings.credential_enc_key = base64.b64encode(b"tooshort").decode()
    crypto._key = None
    with pytest.raises(ValueError):
        crypto.encrypt_credential({"k": "v"})
