"""受管浏览器抓取：用平台页面自己的请求完成需要运行时签名的接口。

有些平台的接口（如知乎 ``search_v3``）需要页面 JavaScript 生成的签名头。
按开源合规要求，本仓库不打包第三方签名脚本，而是注入已存 Cookie、打开官方
页面，让平台自己的脚本发起请求，我们从响应中取回 JSON。浏览器不可用时抛出
可读异常，由上层决定是否降级。
"""

import asyncio
import json
import logging
from urllib.parse import quote

logger = logging.getLogger("platform.browser_fetch")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _parse_cookie(cookie: str, domain: str) -> list[dict]:
    out = []
    for part in (cookie or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        out.append({"name": k, "value": v, "domain": domain, "path": "/"})
    return out


def _require_playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise RuntimeError(
            "需要安装 Playwright：uv sync --extra browser && uv run playwright install chromium"
        ) from exc
    return async_playwright


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
    async_playwright = _require_playwright()
    captured: list[dict] = []

    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=_UA, viewport={"width": 1400, "height": 900}, locale="zh-CN"
        )
        cookies = _parse_cookie(cookie, domain)
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()

        async def _on_response(resp):
            if api_token not in resp.url:
                return
            try:
                data = await resp.json()
            except Exception:  # noqa: BLE001 非 JSON 响应忽略
                return
            if isinstance(data, dict):
                captured.append(data)

        page.on("response", lambda resp: asyncio.create_task(_on_response(resp)))
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while not captured and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
        if not captured:
            raise RuntimeError("浏览器抓取超时：平台未在限定时间内返回数据接口响应")
        for _ in range(max(0, pages - 1)):
            before = len(captured)
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
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                logger.debug("浏览器关闭失败，已忽略", exc_info=True)
        await pw.stop()

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
