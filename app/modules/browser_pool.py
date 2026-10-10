"""受管浏览器复用池：同一平台复用浏览器上下文，避免每次调用冷启动 Chromium。

替换后抖音/知乎都要靠页面自身完成签名，如果每次 API 调用都新建浏览器，
一次同步会冷启动几十个 Chromium（数秒级开销 + 更重的资源占用）。
本模块按 key 复用 ``BrowserContext``，cookie 变化时重建，空闲则由退出时统一回收。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("platform.browser_pool")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_sessions: dict[str, dict] = {}
_locks: dict[str, asyncio.Lock] = {}


def _require_playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise RuntimeError(
            "需要安装 Playwright：uv sync --extra browser && uv run playwright install chromium"
        ) from exc
    return async_playwright


def _cookie_fingerprint(cookie: str) -> str:
    import hashlib

    return hashlib.sha1((cookie or "").encode("utf-8")).hexdigest()


def parse_cookies(cookie: str, domain: str) -> list[dict]:
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


async def acquire(key: str, *, cookie: str, domain: str):
    """取（或建）某 key 的浏览器上下文。

    返回 ``(context, page)``；cookie 指纹变化时销毁旧上下文重建。
    调用方负责页面导航，不负责关闭（由 ``close_all`` 统一回收）。
    """
    fp = _cookie_fingerprint(cookie)
    sess = _sessions.get(key)
    if sess is not None and sess.get("fingerprint") == fp:
        return sess["context"], sess["page"]
    if sess is not None:
        await _drop(key)

    async_playwright = _require_playwright()
    from app.core import outbound

    pw = await async_playwright().start()
    launch_kwargs = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    proxy = outbound.proxy_for(key)
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    browser = await pw.chromium.launch(**launch_kwargs)
    context = await browser.new_context(
        user_agent=_UA, viewport={"width": 1400, "height": 900}, locale="zh-CN"
    )
    cookies = parse_cookies(cookie, domain)
    if cookies:
        await context.add_cookies(cookies)
    page = await context.new_page()
    _sessions[key] = {
        "pw": pw,
        "browser": browser,
        "context": context,
        "page": page,
        "fingerprint": fp,
    }
    return context, page


def lock_for(key: str) -> asyncio.Lock:
    """取该 key 的页面互斥锁：同一浏览器的页面不能并发导航。"""
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def _drop(key: str) -> None:
    sess = _sessions.pop(key, None)
    if not sess:
        return
    for attr in ("browser", "pw"):
        obj = sess.get(attr)
        if obj is None:
            continue
        try:
            await (obj.close() if attr == "browser" else obj.stop())
        except Exception:  # noqa: BLE001
            logger.debug("关闭浏览器资源失败 %s", attr, exc_info=True)


async def close_all() -> None:
    """释放全部池化浏览器（进程退出/测试清理用）。"""
    for key in list(_sessions):
        await _drop(key)
