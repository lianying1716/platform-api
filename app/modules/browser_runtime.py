"""browser_runtime：受管无头浏览器运行时（PRD 第二阶段专项）。

职责：统一管理 Playwright 浏览器的可用性与生命周期，为以下场景提供地基：
1. 抖音 / 小红书 扫码登录（已由 browser_qr.py 实现，本模块统一暴露与健康检查）。
2. 兜底签名：极少数必须在真实页面 evaluate 才能算的签名（当前 bilibili/douyin/xhs
   已全部纯 Python 抽离，无需浏览器；此处保留接口，未来平台改版时兜底）。

设计原则：
- 浏览器是可选能力。未安装 Playwright/Chromium 时，扫码登录降级为「手动粘 Cookie」，
  关键词抓取不受影响（已纯 Python）。
- 不常驻浏览器进程；按需启动、用完即回收（扫码会话由 browser_qr 的 TTL 管理）。
"""

import logging
import shutil

logger = logging.getLogger("platform.browser_runtime")


def playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def chromium_installed() -> bool:
    """检测 Playwright 的 chromium 二进制是否已下载。"""
    if not playwright_installed():
        return False
    try:
        from playwright._impl._driver import compute_driver_executable  # noqa: F401
    except Exception:
        pass
    # Playwright 默认把浏览器装到 ~/.cache/ms-playwright 或 PLAYWRIGHT_BROWSERS_PATH
    import os
    from pathlib import Path

    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / ".cache" / "ms-playwright")
    p = Path(base)
    if not p.is_dir():
        return False
    return any(p.glob("chromium-*"))


async def health() -> dict:
    """browser_runtime 健康检查：能否真正启动一个无头浏览器并打开 about:blank。"""
    if not playwright_installed():
        return {"ok": False, "detail": "Playwright 未安装（uv sync --extra browser）"}
    if not chromium_installed():
        return {"ok": False, "detail": "Chromium 未下载（uv run playwright install chromium）"}
    try:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto("about:blank")
            await browser.close()
        finally:
            await pw.stop()
        return {"ok": True, "detail": "无头浏览器可正常启动"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"浏览器启动失败: {exc}"}


def status() -> dict:
    """同步快速状态（诊断页用，不真正起浏览器）。"""
    has_node = shutil.which("node") is not None  # 浏览器运行时兜底
    return {
        "playwright": playwright_installed(),
        "chromium": chromium_installed(),
        "node_available": has_node,
    }
