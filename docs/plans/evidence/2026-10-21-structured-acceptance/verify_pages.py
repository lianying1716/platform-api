import asyncio, json, os, sys
from pathlib import Path
from playwright.async_api import async_playwright

BASE = 'http://127.0.0.1:5300'
OUT = Path('docs/plans/evidence/2026-10-21-structured-acceptance')
OUT.mkdir(parents=True, exist_ok=True)
PAGES = [
    ('overview', '/#/overview'), ('accounts', '/#/accounts'),
    ('followed', '/#/followed-creators'), ('crawl', '/#/crawl'),
    ('assets', '/#/assets'), ('pipeline', '/#/pipeline'),
    ('models', '/#/models'), ('ai', '/#/ai'), ('settings', '/#/system'),
]

async def main():
    report = {'passed': True, 'views': [], 'console_errors': [], 'page_errors': [], 'api_5xx': [], 'avatar_422': []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path='/home/ztb/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',
            args=['--no-sandbox'],
        )
        for width, height, label in ((1440, 900, 'desktop'), (390, 844, 'narrow')):
            page = await browser.new_page(viewport={'width': width, 'height': height})
            errors, page_errors, bad, avatars = [], [], [], []
            page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
            page.on('pageerror', lambda e: page_errors.append(str(e)))
            def on_response(response):
                if response.url.startswith(BASE) and '/api/' in response.url and response.status >= 500:
                    bad.append({'url': response.url, 'status': response.status})
                if response.url.startswith(BASE) and '/api/media/avatars/' in response.url and response.status == 422:
                    avatars.append({'url': response.url, 'status': response.status})
            page.on('response', on_response)
            await page.goto(BASE + '/#/login', wait_until='networkidle')
            await page.fill('input[type="password"]', 'Acceptance123!')
            await page.click('button:has-text("进入工作台")')
            await page.wait_for_url('**/#/overview', timeout=15000)
            await page.wait_for_timeout(1500)
            for name, path in PAGES:
                await page.goto(BASE + path, wait_until='networkidle')
                await page.wait_for_timeout(900)
                info = await page.evaluate('''() => ({
                  scrollWidth: document.documentElement.scrollWidth,
                  innerWidth: window.innerWidth,
                  h1: document.querySelector('h1')?.textContent?.trim() || '',
                  body: document.body.innerText.slice(0, 200),
                })''')
                shot = OUT / f'{label}-{name}.png'
                await page.screenshot(path=str(shot), full_page=True)
                overflow = info['scrollWidth'] > info['innerWidth'] + 1
                report['views'].append({'view': name, 'label': label, **info, 'screenshot': str(shot),
                                        'overflow': overflow})
                if overflow or not info['h1']:
                    report['passed'] = False
            report['console_errors'] += errors
            report['page_errors'] += page_errors
            report['api_5xx'] += bad
            report['avatar_422'] += avatars
            if errors or page_errors or bad or avatars:
                report['passed'] = False
            await page.close()
        await browser.close()
    (OUT / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('passed', 'console_errors', 'page_errors', 'api_5xx', 'avatar_422')}, ensure_ascii=False))
    print('views=', len(report['views']))
    sys.exit(0 if report['passed'] else 1)

asyncio.run(main())
