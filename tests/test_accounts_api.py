"""账号中心 API 回归测试。

用独立内存 SQLite + 依赖覆盖，不碰开发/生产库；探活与扫码全部 mock，
不发真实平台请求。覆盖：鉴权、未知平台、CRUD、脱敏、体检、扫码闭环。
"""

import base64
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def client(monkeypatch):
    # 密钥走环境变量，避免落盘
    monkeypatch.setenv("PLATFORM_CREDENTIAL_ENC_KEY", base64.b64encode(os.urandom(32)).decode())

    import app.config as config

    config.settings.credential_enc_key = base64.b64encode(os.urandom(32)).decode()
    import app.core.crypto as crypto

    crypto._key = None

    # 独立内存库
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    import app.db as db
    from app.db import Base

    Base.metadata.create_all(engine)

    def override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    # 调度器在测试里不启动
    monkeypatch.setattr("app.core.scheduler.start_scheduler", lambda: None)
    monkeypatch.setattr("app.core.scheduler.stop_scheduler", lambda: None)
    monkeypatch.setattr(db, "init_db", lambda: None)

    from app.main import app

    app.dependency_overrides[db.get_db] = override_get_db

    with TestClient(app) as c:
        # 初始化管理员并带上 token
        r = c.post("/api/auth/setup", json={"password": "test1234"})
        token = r.json()["token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c

    app.dependency_overrides.clear()


def _mock_check(monkeypatch, ok=True, message="在线", external_id="123", display_name="昵称"):
    from app.modules.base import CheckResult

    async def fake_check(self, credential):
        return CheckResult(ok=ok, message=message, external_id=external_id, display_name=display_name)

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.check", fake_check)


def test_requires_auth():
    """不带 token 必须 401。"""
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/accounts").status_code == 401


def test_unknown_platform_returns_422(client):
    r = client.post("/api/accounts", json={"platform": "nosuch", "credential": {}})
    assert r.status_code == 422


def test_missing_required_field_returns_422(client):
    r = client.post("/api/accounts", json={"platform": "bilibili", "credential": {}})
    assert r.status_code == 422


def test_crud_and_masking(client, monkeypatch):
    _mock_check(monkeypatch)
    # 创建（跳过真实探活）
    r = client.post(
        "/api/accounts",
        json={
            "platform": "bilibili",
            "display_name": "主号",
            "credential": {"sessdata": "fakesessdatavalue", "bili_jct": "csrftoken"},
            "skip_check": True,
        },
    )
    assert r.status_code == 200
    acc = r.json()
    aid = acc["id"]
    # 脱敏：原始值不得出现
    preview = acc["credential_preview"]
    assert "fakesessdatavalue" not in str(preview)
    assert preview["sessdata"] == "fake****alue"

    # 列表
    r = client.get("/api/accounts")
    assert len(r.json()) == 1

    # 体检（mock 返回在线，回填昵称/external_id）
    r = client.post(f"/api/accounts/{aid}/check")
    assert r.json()["ok"] is True
    assert r.json()["status"] == "active"

    # 更新凭证
    r = client.put(
        f"/api/accounts/{aid}/credential",
        json={"credential": {"sessdata": "newsessdatavalue", "bili_jct": "newcsrf"}, "skip_check": True},
    )
    assert r.status_code == 200

    # 删除
    assert client.delete(f"/api/accounts/{aid}").status_code == 200
    assert client.get("/api/accounts").json() == []


def test_check_network_error_marks_error_not_dead(client, monkeypatch):
    """探活抛异常时应标记 error，而不是把账号判死或 500。"""
    async def boom(self, credential):
        raise RuntimeError("网络超时")

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.check", boom)
    r = client.post(
        "/api/accounts",
        json={"platform": "bilibili", "credential": {"sessdata": "x" * 12, "bili_jct": "y"}, "skip_check": True},
    )
    aid = r.json()["id"]
    r = client.post(f"/api/accounts/{aid}/check")
    assert r.status_code == 200
    assert r.json()["status"] == "error"


def test_platforms_listing(client):
    r = client.get("/api/platforms")
    platforms = {p["platform"] for p in r.json()}
    assert {"bilibili", "douyin", "wechat_mp", "xhs"} <= platforms


def test_qr_login_flow(client, monkeypatch):
    """扫码闭环：create 拿二维码 → poll 返回 success → 账号入库。全程 mock 适配器。"""
    from app.modules.base import QrPoll, QrStart

    async def fake_start(self):
        return QrStart(session_data={"qrcode_key": "k"}, qr_image_b64="ZmFrZQ==")

    async def fake_poll(self, session_data):
        return QrPoll(
            status="success",
            message="登录成功",
            credential={"sessdata": "qrsessdata", "bili_jct": "qrjct", "buvid3": "", "dedeuserid": "999", "ac_time_value": ""},
            external_id="999",
            display_name="扫码昵称",
        )

    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.qr_start", fake_start)
    monkeypatch.setattr("app.modules.bilibili.BilibiliAdapter.qr_poll", fake_poll)

    r = client.post("/api/accounts/qr-login", json={"platform": "bilibili"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["qr_image_base64"] == "ZmFrZQ=="

    r = client.get(f"/api/accounts/qr-login/{sid}")
    assert r.json()["status"] == "success"
    aid = r.json()["account_id"]

    # 入库且 external_id 正确回填
    r = client.get(f"/api/accounts/{aid}")
    assert r.json()["external_id"] == "999"
    assert r.json()["status"] == "active"
