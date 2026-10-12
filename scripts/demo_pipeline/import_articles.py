"""把抓到的真实公众号文章写进 Postgres（走平台 import 服务，保证与线上同构）。"""
import json, asyncio, os, sys
from pathlib import Path
ROOT=Path(os.environ.get('PLATFORM_ROOT', Path(__file__).resolve().parents[2])); sys.path.insert(0,str(ROOT))
from app.db import SessionLocal
from app.services import import_service

BUILD_DIR=Path(os.environ.get('DEMO_BUILD_DIR', ROOT / 'data/demo_pipeline'))
arts=json.load(open(BUILD_DIR / 'articles.json'))
ok=[u for u,v in arts.items() if 'error' not in v and (v.get('text') or '')]
print('articles to import:', len(ok))

async def main():
    db=SessionLocal()
    imported=0
    for u in ok:
        try:
            r=await import_service.import_wechat_article(db, u)
            imported+=1
            print(' OK', str(r)[:90])
        except Exception as e:
            print(' FAIL', u[-16:], str(e)[:90])
    db.close()
    print('imported', imported)
asyncio.run(main())
