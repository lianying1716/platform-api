"""网页升级的版本持久化：升级后必须把目标版本写回 .env，杜绝重启后静默回退。

回归背景（收尾审查发现）：updater 只把 PLATFORM_VERSION 当临时环境变量传给
`docker compose`，从不写盘。结果网页升级到 v1.3.0 后，任何一次 `docker compose up -d`
或服务器重启都会按 .env 的默认值（:latest）重建，**静默切回旧/另一版本**。
"""

from pathlib import Path

import pytest

from app.updater import versionstate


def test_read_pinned_version_missing_file(tmp_path):
    assert versionstate.read_pinned_version(tmp_path / ".env") is None


def test_write_then_read_roundtrip(tmp_path):
    env = tmp_path / ".env"
    versionstate.write_pinned_version(env, "v1.3.0")
    assert versionstate.read_pinned_version(env) == "v1.3.0"


def test_write_preserves_other_lines(tmp_path):
    """写版本不能破坏 .env 里的其它配置（这是用户的生产配置）。"""
    env = tmp_path / ".env"
    env.write_text(
        "# 注释\nPLATFORM_CREDENTIAL_ENC_KEY=abc\nPLATFORM_PORT=9100\n", encoding="utf-8"
    )
    versionstate.write_pinned_version(env, "v1.3.0")
    text = env.read_text(encoding="utf-8")
    assert "PLATFORM_CREDENTIAL_ENC_KEY=abc" in text
    assert "PLATFORM_PORT=9100" in text
    assert "# 注释" in text
    assert versionstate.read_pinned_version(env) == "v1.3.0"


def test_write_replaces_existing_version_line(tmp_path):
    """已有 PLATFORM_VERSION 行应被替换，而不是重复追加。"""
    env = tmp_path / ".env"
    env.write_text("PLATFORM_VERSION=v1.0.0\nPLATFORM_PORT=9100\n", encoding="utf-8")
    versionstate.write_pinned_version(env, "v2.0.0")
    text = env.read_text(encoding="utf-8")
    assert text.count("PLATFORM_VERSION") == 1
    assert "PLATFORM_VERSION=v2.0.0" in text
    assert versionstate.read_pinned_version(env) == "v2.0.0"


def test_write_is_created_atomically(tmp_path):
    """写盘必须是原子替换（临时文件 + rename），避免中途崩溃留下半截 .env。"""
    env = tmp_path / ".env"
    env.write_text("PLATFORM_PORT=9100\n", encoding="utf-8")
    versionstate.write_pinned_version(env, "v1.3.0")
    # 目录里不应残留临时文件
    leftovers = [p.name for p in tmp_path.iterdir() if p.name not in (".env",)]
    assert leftovers == [], f"残留临时文件：{leftovers}"


def test_rejects_invalid_version_on_write(tmp_path):
    """非法版本号不得写入 .env（防止把注入串持久化）。"""
    env = tmp_path / ".env"
    with pytest.raises(ValueError):
        versionstate.write_pinned_version(env, "v1.0.0; rm -rf /")


def test_write_falls_back_when_target_is_bind_mount(tmp_path, monkeypatch):
    """`.env` 是单文件 bind mount 时 rename 会 EBUSY，必须退回原地写。

    回归背景：在飞牛 NAS（ZFS）上实测，`os.replace(tmp, '/compose/.env')` 抛
    `[Errno 16] Device or resource busy`——compose 把宿主 .env 以单文件方式挂进
    updater 容器，rename 跨不过挂载点。若不兜底，网页升级会在最后一步"记录版本"
    失败并触发回滚，用户看到升级失败。
    """
    import errno
    import os as _os

    env = tmp_path / ".env"
    env.write_text("PLATFORM_VERSION=v1.0.0\nPLATFORM_PORT=9100\n", encoding="utf-8")

    real_replace = _os.replace

    def fake_replace(src, dst):
        # 只对真正的目标 .env 模拟 EBUSY；清理临时文件时的 replace 不拦。
        if str(dst).endswith(".env"):
            raise OSError(errno.EBUSY, "Device or resource busy", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(versionstate.os, "replace", fake_replace)
    versionstate.write_pinned_version(env, "v1.0.1")

    assert versionstate.read_pinned_version(env) == "v1.0.1"
    assert "PLATFORM_PORT=9100" in env.read_text(encoding="utf-8")
    # 兜底路径也不能残留临时文件
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != ".env"]
    assert leftovers == [], f"残留临时文件：{leftovers}"
