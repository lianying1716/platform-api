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
import re
import secrets
from collections.abc import Iterable
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

_key: bytes | None = None

_SECRET_PATTERNS = (
    # JSON / dict 形式：{"authorization": "Bearer xxx"} / {"api_key": "xxx"}
    re.compile(
        r'(?i)(["\']?(?:authorization|proxy-authorization|x-api-key|api[_-]?key)["\']?\s*:\s*)'
        r'(["\'])(.*?)(\2)'
    ),
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{6,})\b"),
    re.compile(r"\b(AIza[0-9A-Za-z_\-]{20,})\b"),
)


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


def key_is_configured() -> bool:
    """判断加密密钥是否已显式配置且可加载，**不触发自动生成兜底**。

    迁移场景必须用这个而不是 ``key_is_ready()``：后者在密钥缺失时会自动生成
    ``data/secret.key``，会用新密钥覆盖已有凭证的解密能力。
    """
    try:
        if settings.credential_enc_key:
            _decode_key(settings.credential_enc_key)
            return True
        if settings.credential_key_file is not None:
            path = Path(settings.credential_key_file)
            if not path.exists():
                return False
            _decode_key(path.read_text())
            return True
        fallback = settings.data_dir / "secret.key"
        if not fallback.exists():
            return False
        _decode_key(fallback.read_text())
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


def redact_text(text: str, *, extra_secrets: Iterable[str] = ()) -> str:
    """擦除错误/日志文本中的密钥。

    先替换“本次实际使用”的完整密钥（即使上游回显的格式没有 sk-/AIza 前缀），
    再兜底匹配常见鉴权字段和 Key 形状。调用方必须把结果写库或返回。
    """
    out = str(text or "")
    secrets_to_remove = [s for s in extra_secrets if isinstance(s, str) and s]
    for secret in sorted(secrets_to_remove, key=len, reverse=True):
        out = out.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 4:
            out = pattern.sub(r"\1\2[REDACTED]\4", out)
        elif pattern.groups >= 2:
            out = pattern.sub(r"\1[REDACTED]", out)
        else:
            out = pattern.sub("[REDACTED]", out)
    return out
