"""凭证加密：AES-256-GCM。

密钥来源优先级（见 app/config.py）：
  1) 环境变量 PLATFORM_CREDENTIAL_ENC_KEY —— base64 编码的 32 字节，生产首选
  2) settings.credential_key_file 指向的文件 —— 可放项目目录之外
  3) 都没有则在 data_dir/secret.key 自动生成（开发兜底，0600 权限）

密钥绝不入库、绝不进 git。换机器迁移时必须带上密钥（文件或环境变量），否则凭证全部作废。
"""

import base64
import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

_key: bytes | None = None


def _decode_key(text: str) -> bytes:
    raw = base64.b64decode(text.strip())
    if len(raw) != 32:
        raise ValueError("CREDENTIAL_ENC_KEY 必须是 base64 编码的 32 字节（AES-256）")
    return raw


def _load_key() -> bytes:
    global _key
    if _key is not None:
        return _key
    # 1) 环境变量
    if settings.credential_enc_key:
        _key = _decode_key(settings.credential_enc_key)
        return _key
    # 2) 可配置的密钥文件
    if settings.credential_key_file is not None:
        path = Path(settings.credential_key_file)
        if not path.exists():
            raise RuntimeError(
                f"指定的密钥文件不存在: {path}。"
                "请生成: python -c \"import base64,os;print(base64.b64encode(os.urandom(32)).decode())\" 写入该文件"
            )
        _key = _decode_key(path.read_text())
        return _key
    # 3) data_dir 下自动生成（开发兜底）
    fallback = settings.data_dir / "secret.key"
    if fallback.exists():
        _key = _decode_key(fallback.read_text())
    else:
        _key = AESGCM.generate_key(bit_length=256)
        fallback.write_text(base64.b64encode(_key).decode())
        os.chmod(fallback, 0o600)
    return _key


def key_is_ready() -> bool:
    """供诊断接口检查密钥是否可加载。"""
    try:
        _load_key()
        return True
    except Exception:
        return False


def encrypt_credential(payload: dict) -> str:
    """dict -> base64(nonce + ciphertext)"""
    key = _load_key()
    nonce = secrets.token_bytes(12)
    data = json.dumps(payload, ensure_ascii=False).encode()
    ct = AESGCM(key).encrypt(nonce, data, None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_credential(token: str) -> dict:
    key = _load_key()
    raw = base64.b64decode(token)
    nonce, ct = raw[:12], raw[12:]
    data = AESGCM(key).decrypt(nonce, ct, None)
    return json.loads(data)


def mask(value: str, keep: int = 4) -> str:
    """脱敏显示：AB****yz。短值整体打码。"""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 4}{value[-keep:]}"
