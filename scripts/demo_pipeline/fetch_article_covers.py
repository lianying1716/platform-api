"""下载真实文章封面到 cover_cache（供演示站复制）。"""
import json, os, subprocess, urllib.request
from pathlib import Path
from sqlalchemy import create_engine, text
DB=os.environ.get('PLATFORM_DATABASE_URL', 'postgresql+psycopg2://platform:platform@127.0.0.1:5433/platform')
ROOT=Path(os.environ.get('PLATFORM_ROOT', Path(__file__).resolve().parents[2]))
CACHE=Path(os.environ.get('COVER_CACHE', ROOT / 'data/cover_cache'))
CACHE.mkdir(parents=True, exist_ok=True)
eng=create_engine(DB)
with eng.connect() as c:
    rows=c.execute(text("select a.id aid, c.cover_url from content_item c join media_asset a on a.content_id=c.id where c.platform='wechat_mp' order by c.id")).all()
n=0
for aid,url in rows:
    if not url: continue
    try:
        req=urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'https://mp.weixin.qq.com/'})
        b=urllib.request.urlopen(req,timeout=40).read()
        if len(b)<3000: print('too small',aid); continue
        (CACHE/f'{aid}.bin').write_bytes(b)
        (CACHE/f'{aid}.json').write_text(json.dumps({'content_type':'image/jpeg'}),'utf-8')
        n+=1; print('ok',aid,len(b))
    except Exception as e:
        print('FAIL',aid,str(e)[:60])
print('fetched',n)
