"""按平台的可选出站代理：默认关闭；配置后所有出站请求带上代理。"""


def test_default_no_proxy(monkeypatch):
    from app.core import outbound

    monkeypatch.setattr(outbound.settings, "outbound_proxy", "", raising=False)
    assert outbound.proxy_for("douyin") is None


def test_global_proxy_applies_to_all_platforms(monkeypatch):
    from app.core import outbound

    monkeypatch.setattr(outbound.settings, "outbound_proxy", "http://127.0.0.1:8080", raising=False)
    assert outbound.proxy_for("douyin") == "http://127.0.0.1:8080"
    assert outbound.proxy_for("bilibili") == "http://127.0.0.1:8080"


def test_per_platform_proxy_overrides_global(monkeypatch):
    from app.core import outbound

    monkeypatch.setattr(outbound.settings, "outbound_proxy", "http://global:8080", raising=False)
    monkeypatch.setattr(
        outbound.settings, "platform_proxies", {"douyin": "http://dy:9000"}, raising=False
    )
    assert outbound.proxy_for("douyin") == "http://dy:9000"
    assert outbound.proxy_for("xhs") == "http://global:8080"


def test_platform_can_opt_out(monkeypatch):
    from app.core import outbound

    monkeypatch.setattr(outbound.settings, "outbound_proxy", "http://global:8080", raising=False)
    monkeypatch.setattr(
        outbound.settings, "platform_proxies", {"bilibili": "none"}, raising=False
    )
    assert outbound.proxy_for("bilibili") is None


def test_httpx_proxy_kwarg_shape(monkeypatch):
    from app.core import outbound

    monkeypatch.setattr(outbound.settings, "outbound_proxy", "http://p:1", raising=False)
    monkeypatch.setattr(outbound.settings, "platform_proxies", {}, raising=False)
    assert outbound.httpx_proxy_kwargs("douyin") == {"proxy": "http://p:1"}
    monkeypatch.setattr(outbound.settings, "outbound_proxy", "", raising=False)
    assert outbound.httpx_proxy_kwargs("douyin") == {}
