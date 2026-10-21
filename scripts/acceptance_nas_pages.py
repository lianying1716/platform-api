#!/usr/bin/env python3
"""Nine-page / two-viewport regression against a real NAS instance.

Uses real form login and DOM navigation.  Fails on horizontal overflow,
console errors, page errors or API 5xx responses.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PAGES = [
    ("overview", "/#/overview"),
    ("accounts", "/#/accounts"),
    ("followed", "/#/followed-creators"),
    ("crawl", "/#/crawl"),
    ("assets", "/#/assets"),
    ("pipeline", "/#/pipeline"),
    ("models", "/#/models"),
    ("ai", "/#/ai"),
    ("settings", "/#/system"),
]
CHROME = os.environ.get(
    "ACCEPTANCE_CHROME",
    "/home/ztb/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome",
)


async def main() -> int:
    base = os.environ.get("NAS_BASE", "").rstrip("/")
    password = os.environ.get("NAS_PASSWORD", "")
    if not base or not password:
        raise SystemExit("NAS_BASE and NAS_PASSWORD are required")

    out = Path(os.environ.get("ACCEPTANCE_EVIDENCE_DIR", "docs/plans/evidence/2026-10-21-nas-upgrade"))
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"passed": True, "views": [], "console_errors": [], "page_errors": [], "api_5xx": []}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        for width, height, label in ((1440, 900, "1440"), (390, 844, "390")):
            page = await browser.new_page(viewport={"width": width, "height": height})
            page.on("console", lambda m: report["console_errors"].append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
            page.on(
                "response",
                lambda r: report["api_5xx"].append({"url": r.url, "status": r.status})
                if "/api/" in r.url and r.status >= 500
                else None,
            )
            await page.goto(base + "/#/login", wait_until="networkidle")
            await page.fill("input[type=password]", password)
            await page.click('button:has-text("进入工作台")')
            await page.wait_for_url("**/#/overview", timeout=20000)

            for name, path in PAGES:
                await page.goto(base + path, wait_until="networkidle")
                await page.wait_for_timeout(700)
                info = await page.evaluate(
                    """() => ({
                      scrollWidth: document.documentElement.scrollWidth,
                      innerWidth: window.innerWidth,
                      h1: document.querySelector('h1')?.textContent?.trim() || '',
                    })"""
                )
                overflow = info["scrollWidth"] > info["innerWidth"] + 1
                entry = {"view": name, "label": label, **info, "overflow": overflow}
                report["views"].append(entry)
                if overflow or not info["h1"]:
                    report["passed"] = False
                await page.screenshot(path=str(out / f"{label}-{name}.png"), full_page=True)
            await page.close()
        await browser.close()

    if report["console_errors"] or report["page_errors"] or report["api_5xx"]:
        report["passed"] = False
    (out / "nas-pages.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
