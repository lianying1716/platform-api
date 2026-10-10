"""受管浏览器抓取：用平台页面自己的请求完成需要运行时签名的接口。

有些平台的接口（如知乎 ``search_v3``）需要页面 JavaScript 生成的签名头。
按开源合规要求，本仓库不打包第三方签名脚本，而是注入已存 Cookie、打开官方
页面，让平台自己的脚本发起请求，我们从响应中取回 JSON。浏览器不可用时抛出
可读异常，由上层决定是否降级。
"""

import asyncio
import logging
from urllib.parse import quote

logger = logging.getLogger("platform.browser_fetch")

async def fetch_search(
    *,
    search_url: str,
    api_token: str,
    cookie: str,
    domain: str,
    timeout_ms: int = 20_000,
    pages: int = 1,
) -> list[dict]:
    """打开 search_url，监听平台自身对 api_token 的响应，返回已捕获的 JSON 列表。

    页面加载完成后平台会自己带签名头请求搜索接口；我们只读取响应，不复制签名。
    pages > 1 时通过滚动页面触发后续分页请求。
    """
    from app.core import rate_limit

    # 受管浏览器打开页面本身也是一次平台出站访问：先过该平台的统一限速桶。
    platform = "zhihu" if "zhihu.com" in search_url else ""
    if platform:
        await rate_limit.acquire(platform)
    captured: list[dict] = []
    from app.modules import browser_pool

    _context, page = await browser_pool.acquire(platform or "generic", cookie=cookie, domain=domain)

    async def _on_response(resp):
        if api_token not in resp.url:
            return
        try:
            data = await resp.json()
        except Exception:  # noqa: BLE001 非 JSON 响应忽略
            return
        if isinstance(data, dict) and data not in captured:
            captured.append(data)

    def _hook(resp):
        asyncio.create_task(_on_response(resp))

    # 页面来自复用池：监听器必须在本调用结束时摘除，否则会持续泄漏并触发过期回调。
    page.on("response", _hook)
    async with browser_pool.lock_for(platform or "generic"):
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
            while not captured and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.25)
            if not captured:
                raise RuntimeError("浏览器抓取超时：平台未在限定时间内返回数据接口响应")
            for _ in range(max(0, pages - 1)):
                before = len(captured)
                # 后续分页同样触发平台出站请求：逐页过限速桶，避免用滚动绕过保守档。
                if platform:
                    await rate_limit.acquire(platform)
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:  # noqa: BLE001 页面切换中滚动失败可忽略
                    pass
                page_deadline = asyncio.get_running_loop().time() + 5.0
                while len(captured) == before and asyncio.get_running_loop().time() < page_deadline:
                    await asyncio.sleep(0.25)
                if len(captured) == before:
                    break
        finally:
            try:
                page.remove_listener("response", _hook)
            except Exception:  # noqa: BLE001 摘除失败不影响业务
                logger.debug("摘除 response 监听失败", exc_info=True)

    if not captured:
        raise RuntimeError("浏览器抓取未取得任何数据响应")
    return captured


def build_search_url(base: str, keyword: str, sort: str = "") -> str:
    """构造平台搜索页 URL（只用于让页面自己发起签名请求）。"""
    q = quote(keyword, safe="")
    url = f"{base}/search?q={q}&type=content"
    if sort == "latest":
        url += "&sort=created_time"
    return url


async def fetch_until(
    *,
    page_url: str,
    api_token: str,
    cookie: str,
    domain: str,
    platform: str,
    timeout_ms: int = 20_000,
    pages: int = 1,
) -> dict:
    """导航到 page_url，监听平台自身发出的第一个 api_token 响应并返回其 JSON。

    与 ``fetch_search`` 的区别：只取第一个匹配响应，用于详情/评论这类单页数据。
    """
    from app.core import rate_limit
    from app.modules import browser_pool

    await rate_limit.acquire(platform)
    _context, page = await browser_pool.acquire(platform, cookie=cookie, domain=domain)
    captured: list[dict] = []

    async def _on_response(resp):
        if api_token not in resp.url or captured:
            return
        try:
            data = await resp.json()
        except Exception:  # noqa: BLE001
            return
        if isinstance(data, dict):
            captured.append(data)

    def _hook(resp):
        asyncio.create_task(_on_response(resp))

    page.on("response", _hook)
    async with browser_pool.lock_for(platform):
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
            deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
            while not captured and asyncio.get_running_loop().time() < deadline:
                # 评论区常需滚动加载，滚动一次促成平台请求。
                try:
                    await page.evaluate("window.scrollBy(0, 600)")
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(0.4)
            if not captured:
                raise RuntimeError("浏览器抓取超时：平台未在限定时间内返回评论接口响应")
            return captured[0]
        finally:
            try:
                page.remove_listener("response", _hook)
            except Exception:  # noqa: BLE001
                logger.debug("摘除 response 监听失败", exc_info=True)


async def fetch_page_html(
    *,
    page_url: str,
    cookie: str,
    domain: str,
    platform: str,
    wait_ms: int = 1_500,
) -> str:
    """导航到页面并返回渲染后的 HTML（用于解析内嵌 initialState 的详情页）。"""
    from app.core import rate_limit
    from app.modules import browser_pool

    await rate_limit.acquire(platform)
    _context, page = await browser_pool.acquire(platform, cookie=cookie, domain=domain)
    async with browser_pool.lock_for(platform):
        await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(max(0, wait_ms) / 1000)
        return await page.content()
