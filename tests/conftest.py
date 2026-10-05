"""pytest 共享夹具。

目标：`uv run pytest` 裸跑必绿——不依赖本机 PostgreSQL，也不读写真实 data/。
做法：
- 在导入任何 app.* 之前把 PLATFORM_DATABASE_URL 指到临时 SQLite 文件，
  避免模块级 engine（app.db.engine 在 import 时创建）连生产 PG。
- 每个测试用独立的内存 SQLite + 依赖覆盖，互不污染。
"""

import base64
import os
import tempfile

# 必须早于 app.config / app.db 的 import
os.environ["PLATFORM_DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="platform-test-"), "test.db"
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402


def _fresh_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture()
def _db_env(monkeypatch):
    """随机密钥 + 重置加密单例，保证测试之间不串。"""
    monkeypatch.setenv("PLATFORM_CREDENTIAL_ENC_KEY", _fresh_key())
    import app.config as config

    config.settings.credential_enc_key = _fresh_key()
    import app.core.crypto as crypto

    crypto._key = None


def _memory_engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture()
def _engine(_db_env):
    """单个内存 SQLite 引擎：session 与 client 共享，才能跨两者断言。"""
    engine = _memory_engine()
    import app.models  # noqa: F401  注册全部模型
    from app.db import Base

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def session(_engine):
    """已建全部表的内存 SQLite 会话（与 client 同一库）。"""
    TestSession = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def _app_with_overrides(_db_env, _engine, monkeypatch):
    """把 app 的 get_db 覆盖到独立内存库；关闭调度器与 init_db 连库。"""
    TestSession = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)

    import app.db as db_mod
    import app.main as main_mod

    def override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    # main.py 用 from ... import 直接绑定了这些名字，需同时打补丁
    monkeypatch.setattr(main_mod, "init_db", lambda: None)
    monkeypatch.setattr(main_mod, "start_scheduler", lambda: None)
    monkeypatch.setattr(main_mod, "stop_scheduler", lambda: None)
    main_mod.app.dependency_overrides[db_mod.get_db] = override_get_db
    yield main_mod.app
    main_mod.app.dependency_overrides.clear()


@pytest.fixture()
def anon_client(_app_with_overrides):
    """未登录客户端（验证 401）。"""
    with TestClient(_app_with_overrides) as c:
        yield c


@pytest.fixture()
def client(_app_with_overrides):
    """已初始化管理员并带 token 的客户端。"""
    with TestClient(_app_with_overrides) as c:
        r = c.post("/api/auth/setup", json={"password": "test1234"})
        assert r.status_code == 200, r.text
        c.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
        yield c
