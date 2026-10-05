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


def test_bad_key_length_rejected(monkeypatch):
    import app.config as config
    import app.core.crypto as crypto

    config.settings.credential_enc_key = base64.b64encode(b"tooshort").decode()
    crypto._key = None
    with pytest.raises(ValueError):
        crypto.encrypt_credential({"k": "v"})
