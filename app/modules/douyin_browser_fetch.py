"""抖音「浏览器兜底抓取」：在受管无头浏览器里用同源 fetch 调平台接口。

为什么需要它
------------
抖音部分接口（我的收藏 ``/aweme/v1/web/aweme/listcollection``、点赞
``/aweme/v1/web/aweme/favorite``、收藏短剧 ``/aweme/v1/web/series/aweme``）在纯
HTTP 请求下会被 WAF 直接挡掉——HTTP 200 但响应体是字符串 ``blocked``：

    POST /aweme/v1/web/aweme/listcollection -> 200 "blocked"

这几个接口带空 a_bogus/uifid 也不行，缺的是页面安全 SDK 在运行时生成的签名。
但**在真实页面里用同源 fetch 调同一个接口，平台 SDK 会自己把头和签名补齐**：
实测 POST listcollection 返回 200 + 正常 aweme_list JSON。无需用户重新扫码。

做法
----
把账号已存的 Cookie 注入一个全新的无头浏览器上下文，打开抖音首页，再用
``page.evaluate`` 发同源 fetch（GET/POST 都可）。无需用户交互。

代价：每次调用要起一次浏览器（数秒），所以只在"纯 HTTP 被拦"时才走这条路。
"""

import asyncio
import json
import logging
from urllib.parse import urlencode

logger = logging.getLogger("platform.douyin.browser_fetch")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
)

# 与纯 HTTP 路径保持一致的页面参数（少了会被平台当成异常请求）
_PAGE_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "pc_libra_divert": "Windows",
    "cookie_enabled": "true",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_online": "true",
    "engine_name": "Blink",
    "os_name": "Windows",
    "os_version": "10",
    "device_memory": "8",
    "platform": "PC",
    "downlink": "10",
    "effective_type": "4g",
    "round_trip_time": "0",
    "update_version_code": "170400",
}


def _parse_cookie(cookie: str) -> list[dict]:
    """把 "k=v; k2=v2" 解析成 Playwright 需要的 cookie 对象。"""
    out = []
    for part in (cookie or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        out.append({"name": k, "value": v, "domain": ".douyin.com", "path": "/"})
    return out


async def fetch_json(
    path: str,
    params: dict,
    cookie: str,
    *,
    method: str = "GET",
    referer: str = "https://www.douyin.com/",
) -> dict:
    """在受管浏览器里同源 fetch 一个抖音接口，返回解析后的 JSON。

    浏览器不可用时抛 RuntimeError（由上层转成可读错误，不影响其它平台）。
    """
    from app.core import rate_limit

    # 浏览器同源请求同样是抖音出站流量：必须过统一桶，否则会绕过保守限速。
    # 每次调用取一个令牌（含评论翻页、搜索翻页、博主兜底等所有路径）。
    await rate_limit.acquire("douyin")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise RuntimeError("浏览器兜底抓取需要 Playwright（uv sync --extra browser）") from exc

    merged = dict(_PAGE_PARAMS)
    merged.update({k: str(v) for k, v in (params or {}).items() if v is not None})
    qs = urlencode(merged)
    http_method = (method or "GET").upper()
    url = f"{path}?{qs}"

    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=_UA, viewport={"width": 1400, "height": 900}, locale="zh-CN"
        )
        cookies = _parse_cookie(cookie)
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()
        # 必须先打开抖音站点，让页面安全 SDK 初始化（否则同源 fetch 也会被拦）
        await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(1.5)
        result = await page.evaluate(
            """async ([url, method, referer]) => {
                try {
                    const r = await fetch(url, {
                        method, credentials: 'include',
                        headers: {'referer': referer},
                    });
                    const t = await r.text();
                    return {status: r.status, text: t};
                } catch (e) { return {status: 0, text: String(e)}; }
            }""",
            [url, http_method, referer],
        )
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001 关闭失败不影响结果
                logger.debug("浏览器关闭失败，已忽略", exc_info=True)
        await pw.stop()

    status = result.get("status")
    text = result.get("text") or ""
    if status != 200:
        raise RuntimeError(f"浏览器兜底抓取失败（HTTP {status}）: {text[:160]}")
    if text == "blocked":
        # 连同源 fetch 都被挡：不再是纯 HTTP 能修的问题，如实抛出而非静默返空
        raise RuntimeError("浏览器兜底抓取被平台拦截（blocked），请稍后重试或更新 Cookie")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"浏览器兜底抓取返回非 JSON: {text[:160]}") from exc
