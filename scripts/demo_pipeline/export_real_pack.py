"""从 Postgres 导出真实内容包（含视频 + 文章），供演示库注入。"""
import json, os
from pathlib import Path
from sqlalchemy import create_engine, text
DB=os.environ.get('PLATFORM_DATABASE_URL', 'postgresql+psycopg2://platform:platform@127.0.0.1:5433/platform')
BUILD_DIR=Path(os.environ.get('DEMO_BUILD_DIR', 'data/demo_pipeline'))
BUILD_DIR.mkdir(parents=True, exist_ok=True)
OUT=BUILD_DIR / 'real_pack.json'
eng=create_engine(DB)
pack={'items':[]}
with eng.connect() as c:
    rows=c.execute(text("""
      select a.id as src_asset_id, c.platform, c.external_id, c.content_type, c.title, c.author_name, c.author_id,
             c.url, c.cover_url, c.published_at, c.duration_sec, c.auto_category, c.category_confidence,
             a.relative_path, a.video_file,
             t.language, t.model tmodel, t.segments, t.full_text,
             r.summary, r.tags, r.structured,
             v.verdict, v.sections
        from media_asset a join content_item c on c.id=a.content_id
        left join transcript t on t.asset_id=a.id
        left join analysis_result r on r.asset_id=a.id
        left join viral_breakdown v on v.asset_id=a.id
        where a.id>=50 and a.download_status='done'
          and (a.id < 85 or c.platform='wechat_mp')
        order by a.id
    """)).mappings().all()
for r in rows:
    d={k:(str(r[k]) if k=='published_at' and r[k] else r[k]) for k in r.keys()}
    pack['items'].append(d)
OUT.write_text(json.dumps(pack,ensure_ascii=False,indent=1,default=str),'utf-8')
vids=[x for x in pack['items'] if x['content_type']=='video']
arts=[x for x in pack['items'] if x['content_type']=='article']
print('packed videos',len(vids),'articles',len(arts))
print('with transcript:',sum(1 for x in pack['items'] if x['full_text']))
print('with analysis:',sum(1 for x in pack['items'] if x['summary']))
print('with breakdown:',sum(1 for x in pack['items'] if x['verdict']))
