#!/usr/bin/env python3
"""Real-browser acceptance for the NAS upgrade button (OPS-14 / UI-14 helper).

This script never calls the upgrade API directly.  It drives the same page and
buttons a user would: password form -> 系统设置 -> 版本与升级 -> 检查更新 ->
升级到 ... -> 开始升级.

Usage:
    NAS_BASE=http://192.168.2.4:9100 NAS_PASSWORD=... \
        python scripts/acceptance_nas_upgrade.py --phase preflight
    NAS_BASE=... NAS_PASSWORD=... \
        python scripts/acceptance_nas_upgrade.py --phase upgrade

Evidence is written under docs/plans/evidence/2026-10-21-nas-upgrade/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import Page, async_playwright

OUT = Path("docs/plans/evidence/2026-10-21-nas-upgrade")
CHROME = os.environ.get(
    "ACCEPTANCE_CHROME",
    "/home/ztb/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome",
)


def base_url() -> str:
    value = os.environ.get("NAS_BASE", "").strip()
    if not value.startswith(("http://", "https://")):
        raise SystemExit("NAS_BASE 必须是 http(s):// 地址")
    return value.rstrip("/")


def evidence_dir() -> Path:
    path = Path(os.environ.get("ACCEPTANCE_EVIDENCE_DIR", str(OUT)))
    path.mkdir(parents=True, exist_ok=True)
    return path


def password() -> str:
    value = os.environ.get("NAS_PASSWORD", "")
    if not value:
        raise SystemExit("NAS_PASSWORD 未设置")
    return value


async def login_and_open_version(page: Page, base: str) -> None:
    await page.goto(base + "/#/login", wait_until="networkidle")
    await page.fill("input[type=password]", password())
    await page.click('button:has-text("进入工作台")')
    await page.wait_for_url("**/#/overview", timeout=20000)
    await page.click('.ui-nav a:has-text("系统设置")')
    await page.wait_for_selector('h1:has-text("系统设置")', timeout=15000)
    await page.click('.ui-tab:has-text("版本与升级")')
    await page.wait_for_selector(".el-descriptions", timeout=15000)


async def read_version(page: Page) -> dict[str, str]:
    return await page.evaluate(
        """() => {
          const out = {};
          for (const tr of document.querySelectorAll('.el-descriptions__body tr')) {
            const cells = [...tr.querySelectorAll('th,td')].map(x => x.innerText.trim());
            if (cells.length >= 2) out[cells[0]] = cells[1];
          }
          return out;
        }"""
    )


async def check_updates(page: Page) -> str:
    await page.click('button:has-text("检查更新")')
    await page.wait_for_selector("text=有新版本", timeout=40000)
    return await page.locator("body").inner_text()


async def preflight(browser, base: str, report: dict) -> None:
    out = evidence_dir()
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: report["console_errors"].append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
    page.on(
        "response",
        lambda r: report["api_5xx"].append({"url": r.url, "status": r.status})
        if "/api/" in r.url and r.status >= 500
        else None,
    )

    await login_and_open_version(page, base)
    await page.screenshot(path=str(out / "01-login.png"), full_page=True)
    report["version_before"] = await read_version(page)
    report["updater_enabled"] = await page.locator("text=网页一键升级未启用").count() == 0
    if not report["updater_enabled"]:
        report["passed"] = False
    await page.screenshot(path=str(out / "02-version-before.png"), full_page=True)

    text = await check_updates(page)
    await page.screenshot(path=str(out / "03-check-update.png"), full_page=True)
    report["check_update_text"] = text[-800:]
    report["expected_commit_visible"] = "无法确认发行提交" not in text
    if not report["expected_commit_visible"]:
        report["passed"] = False
    await page.close()


async def upgrade(browser, base: str, report: dict) -> None:
    out = evidence_dir()
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: report["console_errors"].append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
    page.on(
        "response",
        lambda r: report["api_5xx"].append({"url": r.url, "status": r.status})
        if "/api/" in r.url and r.status >= 500
        else None,
    )

    await login_and_open_version(page, base)
    await check_updates(page)

    target = page.locator('button:has-text("升级到")')
    report["target"] = (await target.inner_text()).strip()
    await target.click()
    await page.click('.el-message-box button:has-text("开始升级")')
    await page.screenshot(path=str(out / "04-upgrade-progress.png"), full_page=True)

    final = ""
    for _ in range(90):
        await page.wait_for_timeout(10000)
        try:
            await page.reload(wait_until="networkidle")
            if "/login" in page.url or await page.locator("input[type=password]").count():
                await page.fill("input[type=password]", password())
                await page.click('button:has-text("进入工作台")')
                await page.wait_for_url("**/#/overview", timeout=20000)
                await page.click('.ui-nav a:has-text("系统设置")')
                await page.click('.ui-tab:has-text("版本与升级")')
            final = await page.locator("body").inner_text()
            if any(key in final for key in ("升级完成", "升级成功", "失败", "回滚")):
                break
        except Exception:
            continue

    await page.screenshot(path=str(out / "05-upgrade-done.png"), full_page=True)
    report["final_text"] = final[-2000:]
    report["version_after"] = await read_version(page) if final else {}
    if "失败" in final or "回滚" in final:
        report["passed"] = False
    await page.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("preflight", "upgrade"), required=True)
    args = parser.parse_args()

    base = base_url()
    password()
    out = evidence_dir()
    report: dict = {
        "phase": args.phase,
        "base": base,
        "passed": True,
        "console_errors": [],
        "page_errors": [],
        "api_5xx": [],
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        try:
            if args.phase == "preflight":
                await preflight(browser, base, report)
                name = "login-and-check.json"
            else:
                await upgrade(browser, base, report)
                name = "upgrade-run.json"
        finally:
            await browser.close()

    (out / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
