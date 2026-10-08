"""系统设置 · 大模型「测试连接」的验收测试（mock httpx，不联网）。

覆盖：未填 Base URL / 模型名 的 422；连通成功；上游 4xx 的失败翻译；
未保存字段经 body 临时覆盖。对应前端「设置 → 大模型 → 测试连接」按钮。
"""

import pytest


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _fake_client(monkeypatch, resp=None, raise_exc=None, capture=None):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            if capture is not None:
                capture.update({"url": url, "headers": headers, "json": json})
            if raise_exc:
                raise raise_exc
            return resp

    import app.api.system as sys_mod

    monkeypatch.setattr(sys_mod.httpx, "AsyncClient", Client)


@pytest.mark.asyncio
async def test_test_llm_requires_base_url(client, monkeypatch):
    _fake_client(monkeypatch, _Resp(200, {}))
    r = client.post("/api/system/settings/test-llm", json={"llm_base_url": "", "llm_model": "m"})
    assert r.status_code == 422
    assert "Base URL" in r.text


@pytest.mark.asyncio
async def test_test_llm_falls_back_to_default_model(client, monkeypatch):
    """未填模型名时回退到配置默认（gpt-4o-mini），不算错误。"""
    cap = {}
    _fake_client(monkeypatch, _Resp(200, {"choices": [{"message": {"content": "ok"}}]}), capture=cap)
    r = client.post("/api/system/settings/test-llm", json={"llm_base_url": "https://x/v1", "llm_model": ""})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert cap["json"]["model"]  # 有默认模型名被带上


@pytest.mark.asyncio
async def test_test_llm_success(client, monkeypatch):
    cap = {}
    resp = _Resp(200, {"choices": [{"message": {"content": "pong"}}]})
    _fake_client(monkeypatch, resp, capture=cap)
    r = client.post(
        "/api/system/settings/test-llm",
        json={"llm_base_url": "https://api.example.com/v1", "llm_model": "gpt-x", "llm_api_key": "sk-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["reply"] == "pong" and body["model"] == "gpt-x"
    # 校验真正打到的地址与鉴权头
    assert cap["url"] == "https://api.example.com/v1/chat/completions"
    assert cap["headers"]["Authorization"] == "Bearer sk-1"


@pytest.mark.asyncio
async def test_test_llm_upstream_error_is_translated(client, monkeypatch):
    _fake_client(monkeypatch, _Resp(401, {"error": "bad key"}))
    r = client.post(
        "/api/system/settings/test-llm",
        json={"llm_base_url": "https://api.example.com/v1", "llm_model": "m", "llm_api_key": "bad"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "401" in body["message"]


@pytest.mark.asyncio
async def test_test_llm_network_error_is_reported(client, monkeypatch):
    import httpx

    _fake_client(monkeypatch, raise_exc=httpx.ConnectError("boom"))
    r = client.post(
        "/api/system/settings/test-llm",
        json={"llm_base_url": "https://api.example.com/v1", "llm_model": "m"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "连接失败" in body["message"]
