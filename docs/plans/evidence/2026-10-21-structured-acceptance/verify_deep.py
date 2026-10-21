import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright

BASE='http://127.0.0.1:5300'
OUT=Path('docs/plans/evidence/2026-10-21-structured-acceptance')

async def main():
    result={'passed':True,'steps':[],'console_errors':[],'page_errors':[],'api_errors':[]}
    async with async_playwright() as p:
        b=await p.chromium.launch(executable_path='/home/ztb/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',args=['--no-sandbox'])
        for width,height,label in ((1440,900,'desktop'),(390,844,'narrow')):
            pg=await b.new_page(viewport={'width':width,'height':height})
            ce=[]; pe=[]; ae=[]
            pg.on('console',lambda m: ce.append(m.text) if m.type=='error' else None)
            pg.on('pageerror',lambda e: pe.append(str(e)))
            def on_resp(r):
                if r.url.startswith(BASE) and '/api/' in r.url and r.status>=500:
                    ae.append({'status':r.status,'url':r.url})
            pg.on('response',on_resp)
            await pg.goto(BASE+'/#/login',wait_until='networkidle')
            await pg.fill('input[type=password]','Acceptance123!')
            await pg.click('button:has-text("进入工作台")')
            await pg.wait_for_url('**/#/overview',timeout=15000)

            async def snap(name):
                await pg.wait_for_timeout(600)
                sp=OUT/f'{label}-deep-{name}.png'; await pg.screenshot(path=str(sp),full_page=True)
                sw=await pg.evaluate('document.documentElement.scrollWidth'); iw=await pg.evaluate('innerWidth')
                ok=sw<=iw+1
                result['steps'].append({'label':label,'step':name,'ok':ok,'scrollWidth':sw,'innerWidth':iw,'screenshot':str(sp)})
                if not ok: result['passed']=False
                return ok

            # settings tabs: real clicks
            await pg.goto(BASE+'/#/system',wait_until='networkidle'); await pg.wait_for_timeout(900)
            tab_names=['存储目录','模型接入','评论抓取','AI 时段','视频理解','爆款拆解','同步频率','依赖诊断','版本与升级','管理员']
            for name in tab_names:
                await pg.locator('.ui-tab',has_text=name).first.click()
                await pg.wait_for_timeout(350)
                await snap('settings-'+name.replace('/','-'))
            # model page assignment tab
            await pg.goto(BASE+'/#/models',wait_until='networkidle'); await pg.wait_for_timeout(900)
            await snap('models-loaded')
            if await pg.get_by_text('工作分配',exact=True).count():
                await pg.get_by_text('工作分配',exact=True).first.click(); await pg.wait_for_timeout(500)
                await snap('models-assignments')
            # assets: open first card detail if possible
            await pg.goto(BASE+'/#/assets',wait_until='networkidle'); await pg.wait_for_timeout(1000)
            await snap('assets-loaded')
            cards=pg.locator('.asset-card, .ac, [data-asset-id]')
            if await cards.count():
                try:
                    await cards.first.click(); await pg.wait_for_timeout(900)
                    await snap('assets-detail')
                except Exception as e:
                    result['steps'].append({'label':label,'step':'assets-detail','ok':False,'error':str(e)})
                    result['passed']=False
            # pipeline filter click
            await pg.goto(BASE+'/#/pipeline',wait_until='networkidle'); await pg.wait_for_timeout(900)
            if await pg.get_by_text('失败',exact=True).count():
                await pg.get_by_text('失败',exact=True).first.click(); await pg.wait_for_timeout(500)
            await snap('pipeline-filter')
            result['console_errors'] += ce; result['page_errors'] += pe; result['api_errors'] += ae
            if ce or pe or ae: result['passed']=False
            await pg.close()
        await b.close()
    (OUT/'deep-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('passed','console_errors','page_errors','api_errors')},ensure_ascii=False))
    print('steps',len(result['steps']))
    bad=[s for s in result['steps'] if not s.get('ok',False)]
    print('bad',json.dumps(bad,ensure_ascii=False)[:1000])
    sys.exit(0 if result['passed'] else 1)

asyncio.run(main())
