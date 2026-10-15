"""浏览器扫码登录（抖音 / 小红书 / 快手 / 知乎）。

这些平台没有公开扫码 API，方案是：后端起一个 Chromium 打开官方登录页，
截取二维码图片返回给前端/AI 展示，用户手机扫码后从浏览器上下文自动提取全部
Cookie（无需用户手动复制任何东西），随后关闭浏览器。

选择器与成功判定按各平台公开登录页的实际结构维护；登录成功的权威信号是平台写入的
会话 Cookie（抖音另看 localStorage 与跳转 URL）。快手无头模式易触发验证码，需要图形
显示；无 DISPLAY 时进程内起 Xvfb 以有头模式运行。

需要安装 browser 扩展依赖：uv sync --extra browser && uv run playwright install chromium
未安装时返回明确报错，前端引导用户走"手动粘 Cookie"兜底。
"""

import base64
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from app.config import settings
from app.modules.base import QrPoll, QrStart

logger = logging.getLogger("platform.browser_qr")

# 手机确认（check_qrconnect=confirmed）到登录状态落盘的宽限期（秒）。
# 实测确认与写 cookie/localStorage 之间约 1~2s；给足余量但别无限等——
# 超时如实报错，好过永远停在 pending 让用户干等。
_CONFIRMED_GRACE_SECONDS = 15

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
        # 登录信号（任一命中即算成功）：
        # 1. localStorage.HasUserLogin —— 抖音 PC 端登录后主要写这里
        # 2. LOGIN_STATUS cookie —— 部分路径只写 cookie
        # 3. sessionid / sid_tt —— 登录会话 cookie，作为兜底
        # 4. 页面已跳到用户主页/个人页
        "login_cookies": [
            {"name": "LOGIN_STATUS", "value": "1"},
            {"name": "sessionid"},
            {"name": "sid_tt"},
        ],
        "login_local_storage": [{"key": "HasUserLogin", "value": "1"}],
        "login_url_contains": ["/user/self", "/user/", "passport"],
        "logged_out_cookie": {"name": "LOGIN_STATUS", "value": "0"},
        # 扫码进度以平台的 check_qrconnect 为准（实测返回 status: new/scanned/confirmed）。
        # 不要用 passport_csrf_token 之类去猜"已扫码"——它进登录页就有，会误报。
        "qr_status_url": "check_qrconnect",
        "qr_status_map": {"new": "pending", "scanned": "scanned", "confirmed": "success"},
        # 登录后从页面自身取"我是谁"：external_id 决定二次登录是更新还是新建账号，
        # 取不到就会每扫一次建一个新号（实测过）。
        "self_profile_url": (
            "/aweme/v1/web/user/profile/self?device_platform=webapp&aid=6383"
        ),
        "self_id_fields": ["sec_uid"],
        "self_id_cookies": ["sid_tt"],
        "self_name_fields": ["nickname"],
        "needs_display": False,
    },
    "xhs": {
        "url": "https://www.xiaohongshu.com",
        "qr_selector": "xpath=//img[@class='qrcode-img']",
        "panel_selector": "",
        "login_button": "",
        "cookie_domain": "xiaohongshu.com",
        "login_cookies": [{"name": "web_session"}],
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
        "login_cookies": [{"name": "passToken"}],
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
        "login_cookies": [{"name": "z_c0"}],
        "login_value": None,
        "needs_display": False,
        "qr_kind": "canvas",
    },
}

# browser_session_id -> 活浏览器上下文
_live: dict[str, dict] = {}


class BrowserQrUnavailable(RuntimeError):
    pass


def _cookie_hit(cookie_map: dict, rules: list[dict]) -> bool:
    """cookie 规则：命中任一即算（value 未声明时只要存在即可）。"""
    for rule in rules:
        v = cookie_map.get(rule["name"])
        if v is None or v == "":
            continue
        if "value" not in rule or v == rule["value"]:
            return True
    return False


def _is_logged_in(
    platform: str, cookie_map: dict, local_storage: dict | None = None, url: str = ""
) -> bool:
    """多信号判断登录态。

    抖音的登录态不一定写 LOGIN_STATUS cookie（PC 端主要写 localStorage.HasUserLogin），
    所以不能只看一个 cookie，否则扫完码也永远判为未登录、一直停在 pending。
    """
    conf = _PLATFORM_CONF[platform]

    # 明确登出信号优先：LOGIN_STATUS=0 时即使有 sessionid 也视为未登录
    out = conf.get("logged_out_cookie")
    if out and cookie_map.get(out["name"]) == out.get("value", "0"):
        return False

    if cookie_map and _cookie_hit(cookie_map, conf.get("login_cookies", [])):
        return True

    ls = local_storage or {}
    for rule in conf.get("login_local_storage", []):
        v = ls.get(rule["key"])
        if v is None or v == "":
            continue
        if "value" not in rule or str(v) == str(rule["value"]):
            return True

    # 已经跳到个人页也算登录（部分平台登录后直接跳转，cookie 写入有延迟）
    if url:
        for frag in conf.get("login_url_contains", []):
            if frag in url and "login" not in url.split("?")[0]:
                return True
    return False


def _extra_credential(platform: str) -> dict:
    """除 cookie 串外额外回填的凭证字段（抖音需要 sec_user_id/my_user_id）。"""
    if platform == "douyin":
        return {"sec_user_id": "", "my_user_id": ""}
    return {}


def _start_xvfb() -> tuple[subprocess.Popen | None, str | None]:
    """无 DISPLAY 时起一个进程内 Xvfb，返回 (进程, display)。

    Xvfb 只在 **Linux** 有意义：macOS / Windows 由系统窗口服务提供显示，有头模式可直接
    启动，不需要也不会用到 Xvfb（macOS 上即便装了 Xvfb 也不该走这条路）。所以非 Linux
    平台直接返回 (None, None)，由调用方按"平台自带显示"处理。
    已有 DISPLAY 或 Xvfb 不可用也返回 (None, None)。
    """
    if os.environ.get("DISPLAY"):
        return None, None
    if sys.platform != "linux":
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


def _has_captcha_challenge(page) -> bool:
    """页面是否真的出现风控验证码（而非常态加载的反欺诈 SDK）。

    抖音登录页总会加载 ``rmc-nocaptcha`` 反欺诈 SDK iframe，其 URL 含 "captcha" 子串，
    但它不是验证码挑战——必须排除，否则会把正常页面误判成风控。
    """
    for fr in page.frames:
        url = (fr.url or "").lower()
        if "captcha" not in url:
            continue
        if "nocaptcha" in url or "no-captcha" in url:
            continue  # 常态反欺诈 SDK，不是挑战
        return True
    return False


async def _qr_image_b64(page, selector: str) -> str:
    """等待二维码元素并返回 base64 PNG。

    轮询等待可容忍慢加载（快手首屏较慢）；只有**真正等不到二维码**时才判定原因：
    命中风控验证码 / 页面结构变化。

    注意：抖音登录页**总是**加载一个 ``rmc-nocaptcha``（反欺诈 SDK）iframe，其 URL 里
    含 "captcha" 子串但那并非验证码挑战。早期版本用 ``"captcha" in fr.url`` 在重试循环里
    立刻抛错，会把这种常态 iframe 误判成风控（二维码其实马上就出来了），表现为"抖音扫码
    时好时坏"。这里改为：忽略 nocaptcha，且只在最终拿不到二维码时才报风控。
    """
    el = None
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            el = await page.wait_for_selector(selector, timeout=4_000)
            break
        except Exception:
            continue
    if el is None:
        if _has_captcha_challenge(page):
            raise BrowserQrUnavailable(
                "平台触发了风控验证码（当前网络/出口 IP 被标记），无法获取二维码。"
                "请稍后重试或改用「手动粘 Cookie」方式录入。"
            )
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


async def _read_qr_image_wait(page, selector: str, timeout_ms: int = 3_000) -> str | None:
    """读当前二维码，容忍短暂的空窗（换码时元素会消失 1~2s）。

    与 ``_read_qr_image_now`` 的区别：那个是"立刻返回、没有就 None"，适合判定
    "这一轮有没有变化"；本函数会小等一下，适合**自愈换图**——否则恰好撞上换码
    空白期就会误判成"页面没码"，把用户屏幕上的好码也判成失效。
    """
    try:
        await page.wait_for_selector(selector, timeout=timeout_ms)
    except Exception:  # noqa: BLE001 超时就当这一轮没有，交给调用方保持现状
        return None
    return await _read_qr_image_now(page, selector)


async def _read_qr_image_now(page, selector: str) -> str | None:
    """同步读取当前二维码（不等待）。

    平台自带的二维码会过期并被登录页自动换成新的一张（抖音实测约 60s 一换、换后 status
    立刻回到 new）。轮询遇到"平台自然过期"时用这个立刻抓当前这张图回传给前端换图，
    让用户能继续扫——而不是把整个会话判死、逼用户重新点一次。
    """
    try:
        el = await page.query_selector(selector)
        if el is None:
            return None
        src = str(await el.get_property("src"))
    except Exception:  # noqa: BLE001 页面跳转/元素消失时取不到，交给调用方按过期处理
        return None
    if src.startswith("data:image"):
        return src.split(",", 1)[1]
    if src.startswith("http"):
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _UA}, follow_redirects=True, timeout=15
            ) as client:
                resp = await client.get(src)
            return base64.b64encode(resp.content).decode()
        except Exception:  # noqa: BLE001
            return None
    try:
        return base64.b64encode(await el.screenshot()).decode()
    except Exception:  # noqa: BLE001
        return None


async def browser_qr_start(platform: str) -> QrStart:
    conf = _PLATFORM_CONF[platform]
    async_playwright = _require_playwright()

    needs_display = bool(conf.get("needs_display"))
    xvfb_proc = None
    launch_env = None
    if needs_display:
        # macOS / Windows 自带窗口服务，有头模式可直接启动，不依赖 DISPLAY / Xvfb。
        # 只有 Linux 在无 DISPLAY 时才需要临时起一个 Xvfb。
        if sys.platform == "linux" and not os.environ.get("DISPLAY"):
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
        launch_kwargs = {
            "headless": headless,
            "env": launch_env,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        from app.core import outbound

        proxy = outbound.proxy_for(platform)
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        browser = await pw.chromium.launch(
            **launch_kwargs,
        )
        context = await browser.new_context(
            user_agent=_UA,
            viewport={"width": 1400, "height": 900},
            locale="zh-CN",
            # UA 声明的是中国 Windows 用户，时区/语言必须跟着一致：UA 说 Windows、
            # 时区却是 UTC、语言只有 en-US —— 这是风控最典型的"非真人"指纹组合。
            timezone_id="Asia/Shanghai",
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        try:
            from playwright_stealth import Stealth

            await Stealth().apply_stealth_async(context)
        except ImportError:
            # 反检测是增强项：没装 browser extra 时仍可继续扫码流程。
            pass
        # ⚠️ stealth 会把 navigator.languages 覆盖成 ["en-US","en"]（实测），把一个
        # "中文 Windows 用户"变成"英文 Windows 用户"——与 UA/时区矛盾，反而更像机器人。
        # 在 stealth 之后纠正回来，并补齐无头环境缺失的指纹（deviceMemory 为 null、
        # 插件为空等，都是无头特征）。
        await _apply_consistent_fingerprint(context)
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
        # 记下当前二维码指纹：轮询遇到"平台自然过期"时，只有页面上的图确实换新了
        # （抖音过期后约 2s 自动换成新码）才把新图回传，避免把同一张已被判废的死码再发一次。
        _qr_hash = hashlib.md5(qr_b64.encode()).hexdigest()
        # qr_hash = 页面当前码指纹（用于过期换码判定）；shown_hash = 最后一次**发给前端**
        # 的码指纹。两者不等就说明用户屏幕上那张已经不是页面在轮询的那张——扫码必然失败。
        state: dict = {"qr_status": "", "qr_hash": _qr_hash, "shown_hash": _qr_hash}
        _live[session_id] = {
            "pw": pw,
            "browser": browser,
            "context": context,
            "page": page,
            "created": time.time(),
            "xvfb": xvfb_proc,
            "state": state,
        }
        _attach_qr_status_listener(page, conf, state)
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


def _attach_qr_status_listener(page, conf: dict, state: dict) -> None:
    """监听平台的扫码状态端点，把最新 status 记进 state。

    抖音的 check_qrconnect 会返回 status: new（未扫）/ scanned（已扫待确认）/
    confirmed（已确认）。这是最权威的扫码进度来源，比自己猜 cookie 可靠得多。
    """
    token = conf.get("qr_status_url")
    if not token:
        return
    import asyncio as _asyncio

    async def _on_response(resp):
        if token not in resp.url:
            return
        try:
            data = await resp.json()
        except Exception:  # noqa: BLE001 非 JSON/已关闭，忽略
            return
        # 记下页面自己轮询用的 token（来自请求体）——与"二维码里的 token"比对，
        # 能直接暴露"手机确认的是旧 token、页面却在轮询新 token"这类错位。
        try:
            body = resp.request.post_data or ""
            for part in body.split("&"):
                if part.startswith("token="):
                    state.setdefault("poll_tokens", [])
                    state["poll_tokens"].append(part.split("=", 1)[1][:12])
                    break
        except Exception:  # noqa: BLE001
            pass
        inner = (data or {}).get("data") or {}
        st = inner.get("status")
        if st:
            state["qr_status"] = str(st)
        # 抖音在需要二次验证时会丢掉 status、改回 error_code=2046 + 文案
        # （"为保障你的账号安全，请前往抖音APP完成验证后再进行登录"）。
        # 只认 status 会把这个错误吞掉，界面继续显示"已扫码待确认"，误导用户。
        code = inner.get("error_code")
        if code not in (None, 0):
            state["qr_error_code"] = code
            state["qr_error_desc"] = str(inner.get("description") or "")
        state["qr_seen_at"] = time.time()
        # 原始响应留一份（含 error_code/description/extra），真机卡住时靠它定位
        state["qr_raw"] = {
            "status": inner.get("status"),
            "error_code": inner.get("error_code"),
            "description": str(inner.get("description") or "")[:120],
            "extra": str(inner.get("extra") or "")[:120],
        }

    def _hook(resp):
        # 响应体只能异步读，这里起个 task，不阻塞页面
        _asyncio.create_task(_on_response(resp))

    try:
        page.on("response", _hook)
    except Exception:  # noqa: BLE001 挂不上监听不影响主流程（退化为 cookie 判定）
        pass


# 与 UA（Windows Chrome）自洽的指纹。无头 Chromium 默认缺这些，
# 缺得越多越像机器人；stealth 又会把语言改成 en-US，所以要在这里统一收口。
_FINGERPRINT_JS = """
(() => {
  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
    Object.defineProperty(navigator, 'language', { get: () => 'zh-CN' });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
  } catch (e) {}
  try {
    // 无头环境 plugins 常为空；伪造几个常见项，避免"零插件"这个显眼特征。
    if (!navigator.plugins || navigator.plugins.length === 0) {
      const fake = [
        { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
        { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer' },
      ];
      Object.defineProperty(navigator, 'plugins', {
        get: () => Object.assign(fake, { length: fake.length }),
      });
    }
  } catch (e) {}
  try {
    if (!navigator.mimeTypes || navigator.mimeTypes.length === 0) {
      const mime = [{ type: 'application/pdf', suffixes: 'pdf' }];
      Object.defineProperty(navigator, 'mimeTypes', {
        get: () => Object.assign(mime, { length: mime.length }),
      });
    }
  } catch (e) {}
})();
"""


async def _apply_consistent_fingerprint(context) -> None:
    """给整个浏览器上下文注入与 UA 自洽的指纹（语言/内存/插件）。

    必须**在 stealth 之后**调用：stealth 会把 languages 覆盖成 en-US，
    与 UA 声称的"中国 Windows 用户"矛盾。注入失败不影响主流程。
    """
    try:
        await context.add_init_script(_FINGERPRINT_JS)
        # 对当前已打开的页面也立即生效（add_init_script 只影响之后的导航）。
        for page in context.pages:
            try:
                await page.evaluate(_FINGERPRINT_JS)
            except Exception:  # noqa: BLE001 页面还没导航完成时忽略
                pass
    except Exception:  # noqa: BLE001 指纹是增强项，失败不该挡住扫码
        pass


async def _read_local_storage(page) -> dict:
    """读页面 localStorage；页面正在跳转/跨域时可能失败，失败当空处理。"""
    try:
        ls = await page.evaluate("() => Object.fromEntries(Object.entries(window.localStorage))")
        return ls if isinstance(ls, dict) else {}
    except Exception:  # noqa: BLE001 读不到就当作没有，不影响判定
        return {}


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
    page = sess["page"]
    cookies = await context.cookies()
    cookie_map = {c["name"]: c["value"] for c in cookies if conf["cookie_domain"] in c.get("domain", "")}
    local_storage = await _read_local_storage(page)

    if not _is_logged_in(platform, cookie_map, local_storage, page.url):
        st = sess.setdefault("state", {})
        # 页面当前活跃的码只读一次，诊断与"自愈换图"共用（避免一轮里重复截两次图）。
        page_now = await _read_qr_image_wait(page, conf["qr_selector"])
        page_hash = hashlib.md5(page_now.encode()).hexdigest() if page_now else ""
        shown = st.get("shown_hash") or ""
        # 诊断快照：状态变化**或二维码轮换**时各记一条（而不是每轮都记）——
        # 否则上限会被一串重复的 new 吃光，关键的 scanned→confirmed 那一步反而看不到。
        # 二维码轮换必须记：抖音约 60s 换一张，若用户扫的是换掉前的那张，
        # 页面已经在轮询新 token，就只会一直回 new —— 这正是"扫了没反应"的形态。
        # 另外把 cookie 全名与 localStorage 全键也记上，避免"信号存在但没被识别"。
        _rotated = bool(st.get("dbg_last_page_hash")) and st.get("dbg_last_page_hash") != page_hash
        _seen = st.get("qr_seen_at")
        _age = (time.time() - _seen) if _seen else -1
        # 心跳：每 30s 至少记一条，用来区分「页面还在轮询但抖音不回 confirmed」
        # 与「页面自己停了轮询」——只看状态变化日志分不出这两者。
        _hb = (time.time() - st.get("dbg_last_beat", 0)) > 30
        if (
            st.get("qr_status") != st.get("dbg_last_status")
            or _rotated
            or _hb
            or st.get("dbg_logged", 0) == 0
        ):
            st["dbg_logged"] = st.get("dbg_logged", 0) + 1
            st["dbg_last_status"] = st.get("qr_status")
            st["dbg_last_page_hash"] = page_hash
            st["dbg_last_beat"] = time.time()
            conf_c = _PLATFORM_CONF.get(platform, {})
            _toks = st.get("poll_tokens") or []
            logger.info(
                "qr_poll[%s] status=%s age=%.0fs polls=%d raw=%s poll_tokens=%s "
                "url=%s cookie_names=%s ls_keys=%s login_cookies_hit=%s "
                "page_hash=%s shown_hash=%s",
                platform,
                st.get("qr_status", ""),
                _age,
                len(_toks),
                st.get("qr_raw", {}),
                sorted(set(_toks))[-3:],
                (page.url or "")[:70],
                sorted(cookie_map.keys()),
                sorted(local_storage.keys())[:20],
                {r["name"]: cookie_map.get(r["name"]) for r in conf_c.get("login_cookies", [])},
                page_hash[:8] or "-",
                shown[:8] or "-",
            )
        # 平台要求"去 App 完成验证"（抖音 2046 等）：这是硬拦截，继续等下去也不会有
        # 结果，必须如实告诉用户去 App 验证，而不是让他对着"已扫码待确认"干等。
        risk_code = st.get("qr_error_code")
        if risk_code and risk_code != 0:
            await _close(session_id)
            desc = st.get("qr_error_desc") or "平台要求完成安全验证"
            return QrPoll(
                status="error",
                message=f"{desc}（抖音已拦截本次扫码登录，请在手机抖音 App 内完成验证后重试）",
            )
        # 扫码进度优先用平台自己的状态端点（抖音 check_qrconnect），
        # 比猜 cookie 准确：passport_csrf_token 进页面就有，用它判"已扫码"会误报。
        remote = st.get("qr_status", "")
        mapped = (conf.get("qr_status_map") or {}).get(remote, "")
        if mapped == "scanned":
            return QrPoll(status="scanned", message="已扫码，请在手机上点击确认")
        if mapped == "success":
            # 手机已确认（check_qrconnect=confirmed），但 cookie/localStorage 还没落盘
            # ——两者之间通常差 1~2 秒。旧逻辑没有这个分支：confirmed 会掉到最后的
            # 兜底 `pending("等待扫码…")`，用户明明确认过了，界面不但不前进、提示还
            # 倒退成「请用手机扫描二维码」，看起来就是点了没反应。
            # 这里给一小段宽限期等落盘；超过宽限如实报错（而不是永远 pending 骗用户）。
            first = st.setdefault("confirmed_at", time.time())
            if time.time() - first <= _CONFIRMED_GRACE_SECONDS:
                # 单独一个 status：前端据此给"已完成确认"的视觉反馈，
                # 不能和"等待扫码"混在 pending 里（用户会以为没反应）。
                return QrPoll(status="confirmed", message="已在手机确认，正在获取登录状态…")
            return QrPoll(
                status="error",
                message="已确认，但迟迟拿不到登录状态，请关闭后重新获取二维码",
            )
        # 尚未扫码：先把「页面当前活跃的码」对齐到用户屏幕上那张，再决定回什么。
        #
        # 背景（macOS 实测：扫码后界面毫无反应、抖音一直回 status=new）：
        # 抖音登录页的二维码会自然过期并**自动换新**。旧逻辑只在观察到远端 expired
        # 时才把新图发一次；一旦这次轮换没被观察到（事件错过 / 换码与轮询错开），
        # 用户屏幕上就一直挂着页面早已不再轮询的那张死码——手机扫它，抖音永远回 new，
        # 表现为"扫了没反应"，且一直持续到 5 分钟会话超时。
        # 改为每轮都用页面当前码对齐 shown_hash：只要两者不一致就把新图回传，
        # 屏幕上的码始终等于页面正在轮询的码，从根上消掉这个状态。
        if page_now and page_hash != shown:
            st["qr_hash"] = page_hash
            st["shown_hash"] = page_hash
            st.pop("qr_status", None)
            was_expired = remote in ("expired", "canceled", "cancelled")
            return QrPoll(
                status="pending",
                message=(
                    "二维码已刷新，请用手机 App 重新扫描"
                    if was_expired
                    else "二维码已更新，请以当前页面上的码为准"
                ),
                qr_image_b64=page_now,
            )
        if remote in ("expired", "canceled", "cancelled"):
            # 页面当前码与屏幕上一致、但平台说它已过期：等下一轮换码（约 1~2s），
            # 别把这段短暂窗口判死。
            return QrPoll(status="pending", message="二维码正在刷新，请稍候…")
        return QrPoll(status="pending", message="等待扫码（请用手机 App 扫描二维码）")

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_map.items())
    credential = {"cookie": cookie_str}
    credential.update(_extra_credential(platform))
    external_id, display_name = "", ""
    # 抖音：登录后取自己的身份（sec_uid / 昵称）。
    # external_id 用于"同一个人二次登录时更新而非新建账号"，必须拿到。
    if platform == "douyin":
        identity = await _douyin_profile(page, local_storage, cookie_map, conf)
        credential["sec_user_id"] = identity.get("sec_uid", "")
        credential["my_user_id"] = identity.get("uid", "")
        external_id = identity.get("sec_uid", "") or identity.get("uid", "")
        display_name = identity.get("nickname", "")
        if not external_id:
            # 退一步：至少用 sid_tt 当稳定标识，避免每次扫码都新建账号
            external_id = str(cookie_map.get("sid_tt") or "")
    await _close(session_id)
    return QrPoll(
        status="success",
        message="登录成功，已自动提取 Cookie",
        credential=credential,
        external_id=external_id,
        display_name=display_name,
    )


async def _douyin_profile(page, local_storage: dict, cookie_map: dict, conf: dict) -> dict:
    """取抖音"我是谁"：优先用页面自身发起的 self profile 请求（带平台签名），失败则退到 cookie。

    返回 {sec_uid, uid, nickname}；取不到的字段留空，绝不抛异常影响登录。
    """
    out = {"sec_uid": "", "uid": "", "nickname": ""}
    url = conf.get("self_profile_url")
    if url:
        try:
            data = await page.evaluate(
                """async (u) => {
                    try {
                        const r = await fetch(u, {credentials: 'include'});
                        return await r.json();
                    } catch (e) { return null; }
                }""",
                url,
            )
            user = (data or {}).get("user") or {}
            if user:
                for f in conf.get("self_id_fields", []):
                    if user.get(f):
                        out["sec_uid"] = str(user[f]); break
                if user.get("uid"):
                    out["uid"] = str(user["uid"])
                for f in conf.get("self_name_fields", []):
                    if user.get(f):
                        out["nickname"] = str(user[f]); break
        except Exception:  # noqa: BLE001 取身份失败不影响登录本身
            pass
    # 接口拿不到就退回 localStorage / cookie（字段名以实际页面为准，多个候选都试）
    if not out["sec_uid"]:
        for k in ("user_sec_id", "sec_user_id", "userIdSec"):
            if local_storage.get(k):
                out["sec_uid"] = str(local_storage[k]); break
    if not out["uid"]:
        for k in ("user_id", "uid", "userId"):
            if local_storage.get(k):
                out["uid"] = str(local_storage[k]); break
    if not out["uid"]:
        out["uid"] = str(cookie_map.get("uid_tt") or "")
    return out


def _douyin_identity(local_storage: dict, cookie_map: dict) -> dict:
    """从 localStorage / cookie 里捞出抖音自己的用户标识（取不到就留空，不报错）。"""
    def pick(*keys):
        for k in keys:
            v = local_storage.get(k)
            if v:
                return str(v)
        return ""

    sec = pick("user_sec_id", "sec_user_id", "userIdSec")
    uid = pick("user_id", "uid", "userId") or str(cookie_map.get("uid_tt") or "")
    return {"sec_user_id": sec, "my_user_id": uid}


async def cleanup_expired() -> None:
    """由调度器周期调用，回收超时未完成的浏览器会话。"""
    now = time.time()
    for sid, sess in list(_live.items()):
        if now - sess["created"] > settings.qr_session_ttl:
            await _close(sid)
