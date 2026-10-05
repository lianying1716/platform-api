"""浏览器扫码登录（抖音 / 小红书）。

这两个平台没有公开扫码 API，方案是：后端起一个无头 Chromium 打开官方登录页，
截取二维码图片返回给前端/AI 展示，用户手机扫码后从浏览器上下文自动提取全部
Cookie（无需用户手动复制任何东西），随后关闭浏览器。

选择器与成功判定来源：MediaCrawler media_platform/{douyin,xhs}/login.py（已获授权）。
- 抖音：登录面板 #login-panel-new，二维码 #animate_qrcode_container//img，
  成功标志 Cookie LOGIN_STATUS == "1"
- 小红书：二维码 img.qrcode-img（首页自动弹登录框），成功标志出现 web_session

需要安装 browser 扩展依赖：uv sync --extra browser && uv run playwright install chromium
未安装时返回明确报错，前端引导用户走"手动粘 Cookie"兜底。
"""

import base64
import time
import uuid

import httpx

from app.config import settings
from app.modules.base import QrPoll, QrStart

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_PLATFORM_CONF = {
    "douyin": {
        "url": "https://www.douyin.com",
        "qr_selector": "xpath=//div[@id='animate_qrcode_container']//img",
        "panel_selector": "xpath=//div[@id='login-panel-new']",
        "login_button": "xpath=//p[text() = '登录']",
        "cookie_domain": "douyin.com",
    },
    "xhs": {
        "url": "https://www.xiaohongshu.com",
        "qr_selector": "xpath=//img[@class='qrcode-img']",
        "panel_selector": "",
        "login_button": "",
        "cookie_domain": "xiaohongshu.com",
    },
}

# browser_session_id -> 活浏览器上下文
_live: dict[str, dict] = {}


class BrowserQrUnavailable(RuntimeError):
    pass


def _require_playwright():
    try:
        from playwright.async_api import async_playwright

        return async_playwright
    except ImportError as exc:
        raise BrowserQrUnavailable(
            "浏览器扫码需要安装 Playwright：uv sync --extra browser && uv run playwright install chromium。"
            "也可以改用「手动粘 Cookie」方式录入。"
        ) from exc


async def _close(session_id: str) -> None:
    sess = _live.pop(session_id, None)
    if not sess:
        return
    for key in ("browser", "pw"):
        try:
            obj = sess.get(key)
            if obj is None:
                continue
            await (obj.close() if key == "browser" else obj.stop())
        except Exception:
            pass


async def _qr_image_b64(page, selector: str) -> str:
    el = await page.wait_for_selector(selector, timeout=15_000)
    src = str(await el.get_property("src"))
    if src.startswith("data:image"):
        return src.split(",", 1)[1]
    if src.startswith("http"):
        async with httpx.AsyncClient(headers={"User-Agent": _UA}, follow_redirects=True, timeout=15) as client:
            resp = await client.get(src)
            return base64.b64encode(resp.content).decode()
    # 兜底：直接截图二维码元素
    return base64.b64encode(await el.screenshot()).decode()


async def browser_qr_start(platform: str) -> QrStart:
    conf = _PLATFORM_CONF[platform]
    async_playwright = _require_playwright()

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        await page.goto(conf["url"], wait_until="domcontentloaded", timeout=30_000)

        if conf["panel_selector"]:
            # 抖音：登录面板不自动弹出时点一次登录按钮
            try:
                await page.wait_for_selector(conf["panel_selector"], timeout=10_000)
            except Exception:
                try:
                    await page.locator(conf["login_button"]).first.click()
                except Exception:
                    pass

        qr_b64 = await _qr_image_b64(page, conf["qr_selector"])
        session_id = uuid.uuid4().hex
        _live[session_id] = {"pw": pw, "browser": browser, "context": context, "page": page, "created": time.time()}
        return QrStart(session_data={"browser_session": session_id}, qr_image_b64=qr_b64)
    except BrowserQrUnavailable:
        raise
    except Exception:
        try:
            await pw.stop()
        except Exception:
            pass
        raise


async def browser_qr_poll(platform: str, session_data: dict) -> QrPoll:
    conf = _PLATFORM_CONF[platform]
    session_id = session_data.get("browser_session", "")
    sess = _live.get(session_id)
    if sess is None:
        return QrPoll(status="expired", message="扫码会话不存在或已结束，请重新发起")
    if time.time() - sess["created"] > settings.qr_session_ttl:
        await _close(session_id)
        return QrPoll(status="expired", message="二维码已超时，请重新发起")

    context = sess["context"]
    cookies = await context.cookies()
    cookie_map = {c["name"]: c["value"] for c in cookies if conf["cookie_domain"] in c.get("domain", "")}

    logged_in = (
        cookie_map.get("LOGIN_STATUS") == "1" if platform == "douyin" else bool(cookie_map.get("web_session"))
    )
    if not logged_in:
        return QrPoll(status="pending", message="等待扫码（扫码后请在手机上确认）")

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_map.items())
    credential = {"cookie": cookie_str}
    if platform == "douyin":
        # sec_user_id 体检时非必需（无 sec_user_id 走收藏列表探活），后续同步模块再回填
        credential.update({"sec_user_id": "", "my_user_id": ""})
    await _close(session_id)
    return QrPoll(status="success", message="登录成功，已自动提取 Cookie", credential=credential)


async def cleanup_expired() -> None:
    """由调度器周期调用，回收超时未完成的浏览器会话。"""
    now = time.time()
    for sid, sess in list(_live.items()):
        if now - sess["created"] > settings.qr_session_ttl:
            await _close(sid)
