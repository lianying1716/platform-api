"""为缺封面的演示素材生成封面（qwen-image-2.1）。标题驱动，风格统一。"""
import base64, json, os, re, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 任意 OpenAI 兼容的图像生成接口
KEY = os.environ['IMAGE_API_KEY']
API = os.environ.get('IMAGE_BASE_URL', 'https://api.xsai5.xyz/v1') + '/images/generations'
ROOT = Path(os.environ.get('PLATFORM_ROOT', Path(__file__).resolve().parents[2]))
OUT = Path(os.environ.get('COVER_CACHE', ROOT / 'data/cover_cache'))
BUILD_DIR = Path(os.environ.get('DEMO_BUILD_DIR', ROOT / 'data/demo_pipeline'))
OUT.mkdir(parents=True, exist_ok=True)
JOBS = json.load(open(BUILD_DIR / 'need_covers.json', encoding='utf-8'))

# 平台调性 -> 视觉方向（不发散，保证整库看起来像同一个产品采集的）
P = {
 'douyin':  '竖版短视频封面，生活/美食/科技实拍感，主体突出，暖调或高对比，简洁干净',
 'bilibili': '横版视频封面，年轻化科技或生活质感，构图有设计感，色彩明快不花哨',
 'xhs':     '小红书风格竖版封面，清新明亮，柔和自然光，生活方式质感，干净留白',
 'wechat_mp':'横版文章头图，编辑设计感，科技或商务气质，简洁克制',
}
SUFFIX = '画面里不要出现任何文字、logo 或水印。'

def gen(job):
    aid = job['asset_id']; dst = OUT / f'{aid}.bin'
    if dst.exists() and dst.stat().st_size > 30000:
        return (aid, 'skip', 0)
    title = re.sub(r'#\S+', '', job['title']).strip()[:120]
    prompt = f"{P.get(job['platform'], P['douyin'])}。主题：{title}。{SUFFIX}"
    body = {"model":"qwen-image-2.1","prompt":prompt,"width":864,"height":1152,
            "steps":22,"response_format":"b64_json"}
    t0=time.time()
    for attempt in range(2):
        try:
            req=urllib.request.Request(API,data=json.dumps(body).encode(),
                headers={'Authorization':'Bearer '+KEY,'Content-Type':'application/json'})
            d=json.load(urllib.request.urlopen(req,timeout=600))
            b=base64.b64decode(d['data'][0]['b64_json'])
            if len(b) < 20000: raise ValueError('image too small')
            dst.write_bytes(b)
            (OUT/f'{aid}.json').write_text('{"content_type": "image/png"}','utf-8')
            return (aid,'ok',round(time.time()-t0,1))
        except Exception as e:
            if attempt==1: return (aid, f'FAIL {str(e)[:50]}', round(time.time()-t0,1))
            time.sleep(5)

def main():
    print(f'jobs: {len(JOBS)}')
    ok=fail=0
    with ThreadPoolExecutor(max_workers=3) as ex:
        for aid,st,sec in ex.map(gen, JOBS):
            print(f'  {aid:3d} {st} {sec}s', flush=True)
            if st in ('ok','skip'): ok+=1
            else: fail+=1
    print(f'success {ok}/{len(JOBS)} fail={fail}')
main()
