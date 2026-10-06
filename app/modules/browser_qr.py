"""浏览器扫码登录（抖音 / 小红书 / 快手 / 知乎）。

这些平台没有公开扫码 API，方案是：后端起一个 Chromium 打开官方登录页，
截取二维码图片返回给前端/AI 展示，用户手机扫码后从浏览器上下文自动提取全部
Cookie（无需用户手动复制任何东西），随后关闭浏览器。

选择器与成功判定来源：MediaCrawler media_platform/{douyin,xhs,kuaishou,zhihu}/login.py（已获授权）。
- 抖音：登录面板 #login-panel-new，二维码 #animate_qrcode_container//img，
  成功标志 Cookie LOGIN_STATUS == "1"
- 小红书：二维码 img.qrcode-img（首页自动弹登录框），成功标志出现 web_session
- 快手：passport.kuaishou.com 登录页点「扫码」→ img[src^='data:image']（180×180），
  成功标志出现 passToken。**无头必触验证码**，需真实显示：无 DISPLAY 时进程内起 Xvfb 以有头模式运行。
- 知乎：https://www.zhihu.com/signin 的 canvas.Qrcode-qrcode（canvas → 截图），
  成功标志出现 z_c0。无头可用。

需要安装 browser 扩展依赖：uv sync --extra browser && uv run playwright install chromium
未安装时返回明确报错，前端引导用户走"手动粘 Cookie"兜底。
"""

import base64
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

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
        "login_cookie": "LOGIN_STATUS",
        "login_value": "1",
        "needs_display": False,
    },
    "xhs": {
        "url": "https://www.xiaohongshu.com",
        "qr_selector": "xpath=//img[@class='qrcode-img']",
        "panel_selector": "",
        "login_button": "",
        "cookie_domain": "xiaohongshu.com",
        "login_cookie": "web_session",
        "login_value": None,
        "needs_display": False,
    },
    "kuaishou": {
        # 专用 passport 登录页比首页弹窗稳定：首页反复访问会触发 captcha iframe。
        "url": (
            "https://passport.kuaishou.com/pc/account/login/"
            "?sid=kuaishou.web.cp.api&callback=https%3A%2F%2Fwww.kuaishou.com%2F"
        ),
        "qr_selector": "img[src^='data:image']",
        "panel_selector": "",
        # 默认可能是手机号/验证码登录，需切到「扫码」标签页。
        "login_button": "text=扫码",
        "cookie_domain": "kuaishou.com",
        "login_cookie": "passToken",
        "login_value": None,
        # 无头会被 captcha.zt.kuaishou.com 拦截；需真实/虚拟显示 + stealth。
        "needs_display": True,
    },
    "zhihu": {
        "url": "https://www.zhihu.com/signin",
        "qr_selector": "canvas.Qrcode-qrcode",
        "panel_selector": "",
        "login_button": "",
        "cookie_domain": "zhihu.com",
        "login_cookie": "z_c0",
        "login_value": None,
        "needs_display": False,
        "qr_kind": "canvas",
    },
}

# 反检测脚本（来源 MediaCrawler libs/stealth.min.js，随包携带，仅自用学习）。
_STEALTH_JS = Path(__file__).parent / "crawler" / "libs" / "stealth.min.js"

# browser_session_id -> 活浏览器上下文
_live: dict[str, dict] = {}


class BrowserQrUnavailable(RuntimeError):
    pass


def _is_logged_in(platform: str, cookie_map: dict) -> bool:
    """按平台成功标志判断登录态（cookie 名 + 可选期望值）。"""
    conf = _PLATFORM_CONF[platform]
    value = cookie_map.get(conf["login_cookie"])
    expected = conf["login_value"]
    if expected is None:
        return bool(value)
    return value == expected


def _extra_credential(platform: str) -> dict:
    """除 cookie 串外额外回填的凭证字段（抖音需要 sec_user_id/my_user_id）。"""
    if platform == "douyin":
        return {"sec_user_id": "", "my_user_id": ""}
    return {}


def _start_xvfb() -> tuple[subprocess.Popen | None, str | None]:
    """无 DISPLAY 时起一个进程内 Xvfb，返回 (进程, display)。

    已有 DISPLAY 或 Xvfb 不可用则返回 (None, None)（后者由启动 Chromium 时暴露为可读错误）。
    """
    if os.environ.get("DISPLAY"):
        return None, None
    exe = shutil.which("Xvfb")
    if not exe:
        return None, None
    for num in range(90, 120):
        display = f":{num}"
        if Path(f"/tmp/.X{num}-lock").exists():
            continue
        proc = subprocess.Popen(
            [exe, display, "-screen", "0", "1400x900x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)
        if proc.poll() is None:
            return proc, display
    return None, None


async def _stop_xvfb(sess: dict) -> None:
    proc = sess.get("xvfb")
    if proc is not None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
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
    await _stop_xvfb(sess)


async def _qr_image_b64(page, selector: str) -> str:
    """等待二维码元素并返回 base64 PNG。

    轮询等待可容忍慢加载（快手首屏较慢）；若期间出现风控验证码 iframe，抛出可读异常。
    """
    el = None
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            el = await page.wait_for_selector(selector, timeout=4_000)
            break
        except Exception:
            for fr in page.frames:
                if "captcha" in fr.url:
                    raise BrowserQrUnavailable(
                        "平台触发了风控验证码（当前网络/出口 IP 被标记），无法获取二维码。"
                        "请稍后重试或改用「手动粘 Cookie」方式录入。"
                    )
            continue
    if el is None:
        raise BrowserQrUnavailable("未能在登录页找到二维码，平台页面结构可能已变化，请改用「手动粘 Cookie」。")

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

    needs_display = bool(conf.get("needs_display"))
    xvfb_proc = None
    launch_env = None
    if needs_display:
        if os.environ.get("DISPLAY"):
            launch_env = None  # 环境已有显示，直接用
        else:
            xvfb_proc, display = _start_xvfb()
            if xvfb_proc is None or not display:
                raise BrowserQrUnavailable(
                    f"{platform} 扫码需要图形显示（headless 会被平台验证码拦截），"
                    "但系统未安装 Xvfb。请安装 Xvfb 或改用「手动粘 Cookie」方式录入。"
                )
            # 只注入本次 Chromium 启动的环境，不改全局 os.environ（避免并发扫码串扰）。
            launch_env = {**os.environ, "DISPLAY": display}

    headless = not needs_display
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(
            headless=headless,
            env=launch_env,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=_UA, viewport={"width": 1400, "height": 900}, locale="zh-CN"
        )
        if _STEALTH_JS.exists():
            await context.add_init_script(path=str(_STEALTH_JS))
        page = await context.new_page()
        await page.goto(conf["url"], wait_until="domcontentloaded", timeout=30_000)

        # 抖音：登录面板若已自动弹出则无需点击；否则点一次「登录」按钮。
        # 快手：passport 页默认非扫码标签，需点「扫码」切过去（无 panel_selector，必定点击）。
        panel_ready = False
        if conf["panel_selector"]:
            try:
                await page.wait_for_selector(conf["panel_selector"], timeout=6_000)
                panel_ready = True
            except Exception:
                panel_ready = False
        if conf["login_button"] and not panel_ready:
            try:
                # 类人行为：先移动鼠标/滚动，降低触发风控验证码的概率（快手等）。
                try:
                    await page.mouse.move(400, 300)
                    await page.mouse.move(700, 420, steps=12)
                    await page.evaluate("window.scrollBy(0, 180)")
                    await page.wait_for_timeout(800)
                except Exception:
                    pass
                await page.locator(conf["login_button"]).first.click(force=True, timeout=8_000)
                await page.wait_for_timeout(1_500)
            except Exception:
                pass

        qr_b64 = await _qr_image_b64(page, conf["qr_selector"])
        session_id = uuid.uuid4().hex
        _live[session_id] = {
            "pw": pw,
            "browser": browser,
            "context": context,
            "page": page,
            "created": time.time(),
            "xvfb": xvfb_proc,
        }
        return QrStart(session_data={"browser_session": session_id}, qr_image_b64=qr_b64)
    except BrowserQrUnavailable:
        if xvfb_proc is not None:
            await _stop_xvfb({"xvfb": xvfb_proc})
        raise
    except Exception:
        try:
            await pw.stop()
        except Exception:
            pass
        if xvfb_proc is not None:
            await _stop_xvfb({"xvfb": xvfb_proc})
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

    if not _is_logged_in(platform, cookie_map):
        return QrPoll(status="pending", message="等待扫码（扫码后请在手机上确认）")

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_map.items())
    credential = {"cookie": cookie_str}
    # 抖音：sec_user_id 体检时非必需（无 sec_user_id 走收藏列表探活），后续同步模块再回填
    credential.update(_extra_credential(platform))
    await _close(session_id)
    return QrPoll(status="success", message="登录成功，已自动提取 Cookie", credential=credential)


async def cleanup_expired() -> None:
    """由调度器周期调用，回收超时未完成的浏览器会话。"""
    now = time.time()
    for sid, sess in list(_live.items()):
        if now - sess["created"] > settings.qr_session_ttl:
            await _close(sid)
