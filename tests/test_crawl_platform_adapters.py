"""采集平台账号适配器测试（快手/知乎）：注册、凭证解析、体检（mock httpx）。"""

import pytest

def test_crawl_platforms_registered():
    """两家采集平台必须在 REGISTRY 中，否则账号中心无法录入 Cookie。"""
    from app.domain.capabilities import CRAWL_PLATFORMS
    from app.modules import REGISTRY

    for p in ("kuaishou", "zhihu"):
        assert p in REGISTRY, f"{p} 缺少账号适配器"
    assert CRAWL_PLATFORMS - set(REGISTRY) == set()


def test_cookie_adapters_parse_manual_input():
    from app.modules import get_adapter

    for p in ("kuaishou", "zhihu"):
        adapter = get_adapter(p)
        assert adapter.parse_manual_input({"cookie": "A=1; B=2"}) == {"cookie": "A=1; B=2"}
        with pytest.raises(ValueError):
            adapter.parse_manual_input({"cookie": "   "})
        assert adapter.credential_kind == "cookie"


def test_qr_support_only_for_kuaishou_zhihu():
    """快手/知乎支持扫码。"""
    from app.modules import get_adapter

    assert get_adapter("kuaishou").supports_qr is True
    assert get_adapter("zhihu").supports_qr is True


@pytest.mark.asyncio
async def test_kuaishou_qr_delegates_to_browser_qr(monkeypatch):
    from app.modules import browser_qr, get_adapter
    from app.modules.base import QrPoll, QrStart

    calls = {}

    async def fake_start(platform):
        calls["start"] = platform
        return QrStart(session_data={"browser_session": "s"}, qr_image_b64="ZmFrZQ==")

    async def fake_poll(platform, session_data):
        calls["poll"] = (platform, session_data)
        return QrPoll(status="pending", message="等待扫码")

    monkeypatch.setattr(browser_qr, "browser_qr_start", fake_start)
    monkeypatch.setattr(browser_qr, "browser_qr_poll", fake_poll)

    adapter = get_adapter("kuaishou")
    start = await adapter.qr_start()
    assert calls["start"] == "kuaishou" and start.qr_image_b64 == "ZmFrZQ=="
    poll = await adapter.qr_poll(start.session_data)
    assert poll.status == "pending" and calls["poll"][0] == "kuaishou"


@pytest.mark.asyncio
async def test_zhihu_qr_delegates_to_browser_qr(monkeypatch):
    from app.modules import browser_qr, get_adapter
    from app.modules.base import QrStart

    calls = {}

    async def fake_start(platform):
        calls["start"] = platform
        return QrStart(session_data={"browser_session": "s"}, qr_image_b64="ZmFrZQ==")

    monkeypatch.setattr(browser_qr, "browser_qr_start", fake_start)
    start = await get_adapter("zhihu").qr_start()
    assert calls["start"] == "zhihu"


def _fake_httpx(monkeypatch, module, response):
    class Resp:
        def __init__(self):
            self.status_code = 200

        def json(self):
            return response

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return Resp()

        async def post(self, *a, **k):
            return Resp()

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)


@pytest.mark.asyncio
async def test_kuaishou_check_online(monkeypatch):
    from app.modules import crawl_platforms as cp
    from app.modules import get_adapter

    _fake_httpx(monkeypatch, cp, {"data": {"visionProfileUserList": {"result": 1, "hostName": "老铁"}}})
    res = await get_adapter("kuaishou").check({"cookie": "did=1"})
    assert res.ok and res.display_name == "老铁"


@pytest.mark.asyncio
async def test_kuaishou_check_offline(monkeypatch):
    from app.modules import crawl_platforms as cp
    from app.modules import get_adapter

    _fake_httpx(monkeypatch, cp, {"data": {"visionProfileUserList": {"result": 0}}})
    res = await get_adapter("kuaishou").check({"cookie": "did=1"})
    assert not res.ok


@pytest.mark.asyncio
async def test_zhihu_check_online(monkeypatch):
    from app.modules import crawl_platforms as cp
    from app.modules import get_adapter

    _fake_httpx(monkeypatch, cp, {"id": "u1", "name": "张三"})
    res = await get_adapter("zhihu").check({"cookie": "z_c0=1"})
    assert res.ok and res.external_id == "u1" and res.display_name == "张三"

