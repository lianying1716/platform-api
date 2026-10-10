"""按平台的可选出站代理。

默认不启用（单机局域网自用通常直连即可）。需要隔离出口 IP 时，通过环境变量配置：
``PLATFORM_OUTBOUND_PROXY``（全局）或 ``PLATFORM_PLATFORM_PROXIES``（按平台 JSON）。
按平台配置优先；值为 ``none`` 表示该平台即使有全局代理也强制直连。
"""

from __future__ import annotations

from app.config import settings


def proxy_for(platform: str) -> str | None:
    """返回该平台应使用的代理 URL；无则 None。"""
    per_platform = getattr(settings, "platform_proxies", None) or {}
    if platform in per_platform:
        value = str(per_platform.get(platform) or "").strip()
        if value.lower() == "none":
            return None
        return value or None
    global_proxy = str(getattr(settings, "outbound_proxy", "") or "").strip()
    return global_proxy or None


def httpx_proxy_kwargs(platform: str) -> dict:
    """httpx.AsyncClient 的代理参数（无代理时返回空 dict，便于 ** 展开）。"""
    proxy = proxy_for(platform)
    return {"proxy": proxy} if proxy else {}
