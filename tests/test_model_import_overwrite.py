"""覆盖式导入 + 供应商余额/延迟（只手动刷新）。

对应「模型接入改单页弹窗」这一需求的后端契约：

1. `/providers/preview-models`：只拿清单不落库（单页弹窗要先看到并勾选模型）。
2. `/providers/import-models`：一步建/认供应商 + 按勾选**覆盖**模型清单。
3. 覆盖语义：勾选之外的模型被删除；**仍被工作分配引用的不删**，改为停用
   —— 否则外键 SET NULL 会静默清空用户的分配。
4. 余额按 newapi 口径换算（`hard_limit_usd - total_usage/100`），
   且只手动刷新、结果落库。
"""

import json

import pytest
from sqlalchemy import select

from app.models import AiModel, AiProvider, ModelAssignment
from app.modules import model_protocols


def _provider(session, **kw):
    row = AiProvider(
        name=kw.pop("name", "p"),
        preset_key="custom",
        protocol=kw.pop("protocol", "openai"),
        base_url=kw.pop("base_url", "https://api.example.com/v1"),
        api_key_encrypted="cipher",
        enabled=True,
        config_revision=1,
        **kw,
    )
    session.add(row)
    session.commit()
    return row


# ---------------------------------------------------------------- 覆盖语义
def test_import_covers_unchecked_models(client, session):
    """不勾的模型要被删——这是「供应商下线了模型」的修复点。"""
    provider = _provider(session)
    for key in ("m1", "m2", "m3"):
        session.add(AiModel(provider_id=provider.id, model_key=key, capabilities={}))
    session.commit()

    resp = client.post(
        "/api/model-access/models/import",
        json={
            "provider_id": provider.id,
            "models": [{"model_key": "m1"}, {"model_key": "m2"}],
            "probe": {"text": False, "image": False, "video": False},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == ["m3"]
    keys = set(session.scalars(select(AiModel.model_key)).all())
    assert keys == {"m1", "m2"}


def test_import_replaces_on_shrink_and_grow(client, session):
    provider = _provider(session)
    session.add(AiModel(provider_id=provider.id, model_key="old", capabilities={}))
    session.commit()

    resp = client.post(
        "/api/model-access/models/import",
        json={
            "provider_id": provider.id,
            "models": [{"model_key": "new-a"}, {"model_key": "new-b"}],
            "probe": {"text": False, "image": False, "video": False},
        },
    )
    assert resp.status_code == 200, resp.text
    assert set(session.scalars(select(AiModel.model_key)).all()) == {"new-a", "new-b"}


def test_import_keeps_assigned_model_but_disables_it(client, session):
    """仍被工作分配引用的「已下线」模型不能直接删：改为停用并回报。"""
    provider = _provider(session)
    keep = AiModel(provider_id=provider.id, model_key="gone-but-assigned", capabilities={})
    drop = AiModel(provider_id=provider.id, model_key="gone-unused", capabilities={})
    session.add_all([keep, drop])
    session.flush()
    session.add(ModelAssignment(work_type="text_analysis", primary_model_id=keep.id))
    session.commit()

    resp = client.post(
        "/api/model-access/models/import",
        json={
            "provider_id": provider.id,
            "models": [{"model_key": "fresh"}],
            "probe": {"text": False, "image": False, "video": False},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kept_in_use"] == ["gone-but-assigned"]
    assert body["removed"] == ["gone-unused"]
    session.expire_all()
    survived = session.scalar(select(AiModel).where(AiModel.model_key == "gone-but-assigned"))
    assert survived is not None and survived.enabled is False
    assert session.scalar(select(AiModel).where(AiModel.model_key == "gone-unused")) is None
    # 分配没被静默清空。
    assert session.scalar(select(ModelAssignment)).primary_model_id == keep.id


# ---------------------------------------------------------------- 一步式导入
def test_provider_import_creates_then_updates_same_base_url(client, session):
    resp = client.post(
        "/api/model-access/providers/import-models",
        json={
            "name": "我的网关",
            "protocol": "openai",
            "base_url": "https://gw.example.com/v1",
            "api_key": "sk-first",
            "models": [{"model_key": "a"}],
            "probe": {"text": False, "image": False, "video": False},
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(session.scalars(select(AiProvider)).all()) == 1

    # 同地址再来一次：认领同一个供应商，不重复建，且覆盖模型清单。
    resp2 = client.post(
        "/api/model-access/providers/import-models",
        json={
            "protocol": "openai",
            "base_url": "https://gw.example.com/v1",
            "api_key": "sk-second",
            "models": [{"model_key": "b"}],
            "probe": {"text": False, "image": False, "video": False},
        },
    )
    assert resp2.status_code == 200, resp2.text
    assert len(session.scalars(select(AiProvider)).all()) == 1
    assert set(session.scalars(select(AiModel.model_key)).all()) == {"b"}
    # 密钥被更新（加密入库，明文不落库也不回传）。
    provider = session.scalars(select(AiProvider)).one()
    assert "sk-second" not in json.dumps(resp2.json())
    from app.core import crypto

    assert crypto.decrypt_credential(provider.api_key_encrypted)["api_key"] == "sk-second"


def test_provider_import_requires_key_on_create(client, session):
    resp = client.post(
        "/api/model-access/providers/import-models",
        json={
            "protocol": "openai",
            "base_url": "https://nokey.example.com/v1",
            "api_key": "",
            "models": [{"model_key": "a"}],
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------- 余额口径
def test_preview_models_does_not_persist(client, session, monkeypatch):
    async def fake_send(request, *, timeout):
        return {"data": [{"id": "m1"}, {"id": "m2"}]}

    monkeypatch.setattr(model_protocols, "send_request", fake_send)
    resp = client.post(
        "/api/model-access/providers/preview-models",
        json={"protocol": "openai", "base_url": "https://gw.example.com/v1", "api_key": "sk-x"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["models"] == ["m1", "m2"]
    assert session.scalars(select(AiProvider)).all() == []
    assert session.scalars(select(AiModel)).all() == []


def test_parse_balance_follows_newapi_units():
    """`total_usage` 是「分」，要 /100 后再减；单位错会算出负数。"""
    assert model_protocols.parse_balance({"hard_limit_usd": 100.0}, {"total_usage": 2500}) == 75.0
    assert model_protocols.parse_balance({}, {}) == 0.0
    # 星算真实量级：额度 20148.01，已用 1237902.54（分）→ 余额 7768.98 元。
    assert model_protocols.parse_balance(
        {"hard_limit_usd": 20148.01}, {"total_usage": 1237902.54}
    ) == pytest.approx(7768.98, abs=0.01)


def test_balance_requests_only_for_openai_protocol():
    openai = AiProvider(protocol="openai", base_url="https://api.xsai5.xyz/v1")
    assert [k for k, _ in model_protocols.balance_requests(openai, api_key="k")] == [
        "subscription",
        "usage",
    ]
    anthropic = AiProvider(protocol="anthropic", base_url="https://api.anthropic.com")
    assert model_protocols.balance_requests(anthropic, api_key="k") == []


def test_refresh_balance_records_result_and_error(client, session, monkeypatch):
    provider = _provider(session, base_url="https://api.xsai5.xyz/v1")

    async def ok_send(request, *, timeout):
        if request.url.endswith("/subscription"):
            return {"hard_limit_usd": 100.0}
        return {"total_usage": 2500}

    monkeypatch.setattr(model_protocols, "send_request", ok_send)
    # 供应商的 key 是假密文，先塞一个真密文，才能解密。
    from app.core import crypto

    provider.api_key_encrypted = crypto.encrypt_credential({"api_key": "sk-live"})
    session.commit()

    resp = client.post(f"/api/model-access/providers/{provider.id}/refresh-balance")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance_amount"] == 75.0
    assert body["balance_error"] == ""
    assert body["balance_updated_at"]

    async def boom(request, *, timeout):
        raise RuntimeError("gateway said 403")

    monkeypatch.setattr(model_protocols, "send_request", boom)
    resp2 = client.post(f"/api/model-access/providers/{provider.id}/refresh-balance")
    assert resp2.status_code == 200, resp2.text
    # 失败要如实回写，不保留旧数字冒充新结果。
    assert resp2.json()["balance_error"]


def test_refresh_balance_rejects_unsupported_protocol(client, session):
    from app.core import crypto

    provider = _provider(session, protocol="anthropic", base_url="https://api.anthropic.com")
    provider.api_key_encrypted = crypto.encrypt_credential({"api_key": "sk-live"})
    session.commit()
    resp = client.post(f"/api/model-access/providers/{provider.id}/refresh-balance")
    assert resp.status_code == 200, resp.text
    assert resp.json()["balance_error"] == "该协议不支持查询余额"


def test_refresh_latency_measures_and_records_error(client, session, monkeypatch):
    from app.core import crypto

    provider = _provider(session)
    provider.api_key_encrypted = crypto.encrypt_credential({"api_key": "sk-live"})
    session.commit()

    async def ok_send(request, *, timeout):
        return {"data": [{"id": "m1"}]}

    monkeypatch.setattr(model_protocols, "send_request", ok_send)
    resp = client.post(f"/api/model-access/providers/{provider.id}/refresh-latency")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["latency_ms"], int)
    assert resp.json()["latency_error"] == ""

    async def boom(request, *, timeout):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(model_protocols, "send_request", boom)
    resp2 = client.post(f"/api/model-access/providers/{provider.id}/refresh-latency")
    assert resp2.json()["latency_ms"] is None
    assert resp2.json()["latency_error"]


def test_provider_payload_exposes_balance_and_latency_fields(client, session):
    provider = _provider(session)
    resp = client.get("/api/model-access/providers")
    assert resp.status_code == 200, resp.text
    body = resp.json()[0]
    for key in (
        "balance_amount",
        "balance_error",
        "balance_updated_at",
        "latency_ms",
        "latency_error",
        "latency_updated_at",
    ):
        assert key in body, key
    # 从未刷新过 = null（前端显示「—」），不是 0。
    assert body["balance_amount"] is None and body["latency_ms"] is None
