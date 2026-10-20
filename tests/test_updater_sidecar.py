"""updater 边车：共享密钥鉴权 + 版本白名单 + 后台编排 + 只重建 api/worker。

边车持有 docker socket，是升级系统里权限最高的组件，因此安全测试优先。
用注入的假引擎/假依赖测试，不真的动 docker。
"""

import time

import pytest
from fastapi.testclient import TestClient


class _FakeEngine:
    """假引擎：记录 start 调用，模拟后台任务。"""

    def __init__(self):
        self.started = []
        self.job = None
        self._busy = False

    def is_busy(self):
        return self._busy

    def start(self, version, from_version="", reversible=True):
        from app.updater.engine import UpgradeJob

        self.started.append((version, from_version, reversible))
        self.job = UpgradeJob(version=version, from_version=from_version, status="success")
        return self.job

    def latest(self):
        return self.job


def _client(monkeypatch, *, token="s3cr3t", engine=None):
    import app.updater.service as updater_app
    from app.config import settings

    monkeypatch.setattr(settings, "updater_token", token, raising=False)
    monkeypatch.setattr(settings, "updater_url", "http://x", raising=False)
    eng = engine or _FakeEngine()
    monkeypatch.setattr(updater_app, "_engine", eng, raising=False)
    return TestClient(updater_app.app), eng


def test_requires_token(monkeypatch):
    """无/错密钥一律 401，不执行业务。"""
    client, eng = _client(monkeypatch)
    assert client.post("/upgrade", json={"version": "v1.0.0"}).status_code == 401
    assert client.post("/upgrade", json={"version": "v1.0.0"}, headers={"X-Updater-Token": "wrong"}).status_code == 401
    assert eng.started == []


def test_rejects_invalid_version(monkeypatch):
    """非法版本号 422，且绝不执行。"""
    client, eng = _client(monkeypatch)
    r = client.post("/upgrade", json={"version": "v1; rm -rf /"}, headers={"X-Updater-Token": "s3cr3t"})
    assert r.status_code == 422
    assert eng.started == []


def test_accepts_valid_version_returns_accepted(monkeypatch):
    """合法版本 + 正确密钥 → 200 且 accepted=true，后台启动一次。"""
    client, eng = _client(monkeypatch)
    r = client.post("/upgrade", json={"version": "v1.2.3"}, headers={"X-Updater-Token": "s3cr3t"})
    assert r.status_code == 200, r.text
    assert r.json().get("accepted") is True
    # 后台线程启动，稍等
    for _ in range(50):
        if eng.started:
            break
        time.sleep(0.01)
    assert eng.started and eng.started[0][0] == "v1.2.3"


def test_busy_returns_409(monkeypatch):
    """已有升级进行中 → 409。"""
    eng = _FakeEngine()
    eng._busy = True
    client, _ = _client(monkeypatch, engine=eng)
    r = client.post("/upgrade", json={"version": "v1.2.3"}, headers={"X-Updater-Token": "s3cr3t"})
    assert r.status_code == 409


def test_health_no_auth(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.get("/health").status_code == 200


def test_disabled_when_no_token(monkeypatch):
    """未配置密钥 → 服务视为未启用，拒绝所有升级。"""
    client, _ = _client(monkeypatch, token="")
    r = client.post("/upgrade", json={"version": "v1.2.3"}, headers={"X-Updater-Token": "x"})
    assert r.status_code in (401, 409)


def test_status_reports_job(monkeypatch):
    """进度查询返回 busy + job（无需密钥）。"""
    client, eng = _client(monkeypatch)
    eng.start("v9.9.9")
    s = client.get("/upgrade/status")
    assert s.status_code == 200
    body = s.json()
    assert body["job"]["version"] == "v9.9.9"
    assert body["job"]["status"] == "success"


def test_status_empty_before_any_upgrade(monkeypatch):
    client, _ = _client(monkeypatch)
    s = client.get("/upgrade/status")
    assert s.status_code == 200
    assert s.json()["job"] is None
