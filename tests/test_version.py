"""版本单一来源：构建注入优先、dev 回退、版本端点字段齐全。

对应设计 `docs/plans/2026-10-11-versioning-and-upgrade-design.md` 3.1。
"""

import importlib

import pytest
from sqlalchemy import text


def test_build_version_from_env(monkeypatch):
    """构建期注入的版本/commit/时间必须被原样采用。"""
    monkeypatch.setenv("PLATFORM_BUILD_VERSION", "v1.2.3")
    monkeypatch.setenv("PLATFORM_BUILD_COMMIT", "abc1234")
    monkeypatch.setenv("PLATFORM_BUILD_TIME", "2026-10-11T09:45:16Z")
    from app.core import version

    info = version.load_build_info()
    assert info.version == "v1.2.3"
    assert info.commit == "abc1234"
    assert info.build_time == "2026-10-11T09:45:16Z"


def test_dev_fallback_version(monkeypatch):
    """未注入时回退到 pyproject/0.0.0-dev，且不抛异常。"""
    monkeypatch.delenv("PLATFORM_BUILD_VERSION", raising=False)
    monkeypatch.delenv("PLATFORM_BUILD_COMMIT", raising=False)
    from app.core import version

    info = version.load_build_info()
    assert info.version
    assert info.commit  # 至少是 "unknown" 或真实短 commit
    assert version.db_head()  # 内置迁移 head 可解析


def test_version_endpoint_fields(client):
    """GET /api/system/version 返回六个关键字段且 db_in_sync 为布尔。"""
    resp = client.get("/api/system/version")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "app_version",
        "git_commit",
        "build_time",
        "version_source",
        "db_revision",
        "db_head",
        "db_in_sync",
    ):
        assert key in data, key
    assert isinstance(data["db_in_sync"], bool)


def test_version_source_distinguishes_release_from_source_checkout(monkeypatch):
    monkeypatch.setenv("PLATFORM_BUILD_VERSION", "v1.2.3")
    monkeypatch.setenv("PLATFORM_BUILD_COMMIT", "abc1234")
    monkeypatch.setenv("PLATFORM_BUILD_TIME", "2026-10-11T09:45:16Z")
    from app.core import version

    assert version.build_provenance()["source"] == "release_image"
    monkeypatch.delenv("PLATFORM_BUILD_VERSION")
    assert version.build_provenance()["source"] == "source_checkout"


def test_version_endpoint_public(anon_client):
    """版本端点公开只读：未登录也应能查（排障时不该被登录挡住）。"""
    resp = anon_client.get("/api/system/version")
    assert resp.status_code == 200
