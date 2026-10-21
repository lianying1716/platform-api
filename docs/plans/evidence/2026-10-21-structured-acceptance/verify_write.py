import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
BASE='http://127.0.0.1:5300'; OUT=Path('docs/plans/evidence/2026-10-21-structured-acceptance')
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/home/ztb/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',args=['--no-sandbox'])
  pg=await b.new_page(viewport={'width':1440,'height':900})
  logs=[]; bad=[]
  pg.on('console',lambda m: logs.append(m.text) if m.type=='error' else None)
  pg.on('response',lambda r: bad.append((r.status,r.url)) if '/api/' in r.url and r.status>=400 else None)
  await pg.goto(BASE+'/#/login',wait_until='networkidle')
  await pg.fill('input[type=password]','Acceptance123!'); await pg.click('button:has-text("进入工作台")'); await pg.wait_for_url('**/#/overview')
  await pg.goto(BASE+'/#/ai',wait_until='networkidle'); await pg.wait_for_timeout(700)
  await pg.get_by_role('button',name='创建 Key').click(); await pg.wait_for_timeout(300)
  inputs=pg.locator('.el-dialog input')
  await inputs.first.fill('验收临时Key')
  await pg.screenshot(path=str(OUT/'desktop-write-create-dialog.png'),full_page=True)
  await pg.get_by_role('button',name='创建',exact=True).last.click()
  await pg.wait_for_timeout(1200)
  await pg.screenshot(path=str(OUT/'desktop-write-created.png'),full_page=True)
  body=await pg.locator('body').inner_text()
  ok1='验收临时Key' in body
  print('created visible',ok1,'errors',bad[-5:])
  await b.close()
  assert ok1 and not logs and not bad, (ok1,logs,bad)
asyncio.run(main())
