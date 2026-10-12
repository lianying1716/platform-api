"""对真实转录稿跑 deepseek 分析 + 爆款拆解，输出 JSON 包。"""
import json, os, time, urllib.request, urllib.error
from pathlib import Path

# 走 OpenAI 兼容接口。默认指向星算 AI（https://xsai5.xyz，作者自营的闭源模型服务）。
KEY = os.environ['ANALYZE_API_KEY']
URL = os.environ.get('ANALYZE_BASE_URL', 'https://api.xsai5.xyz/v1') + '/chat/completions'
MODEL = os.environ.get('ANALYZE_MODEL', 'deepseek-v4.1-flash-free')
BUILD_DIR = Path(os.environ.get('DEMO_BUILD_DIR', 'data/demo_pipeline'))
SRC = BUILD_DIR / 'transcripts.json'
OUT = BUILD_DIR / 'analysis.json

def llm(prompt, max_tokens=4000, retries=3):
    body = {"model": MODEL, "messages":[{"role":"user","content":prompt}], "max_tokens": max_tokens}
    for a in range(retries):
        try:
            req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                headers={'Authorization':'Bearer '+KEY,'Content-Type':'application/json'})
            r = urllib.request.urlopen(req, timeout=300)
            d = json.load(r)
            return d['choices'][0]['message'].get('content') or ''
        except Exception as e:
            if a == retries-1: raise
            time.sleep(3*(a+1))

def parse_json(txt):
    t = txt.strip()
    if t.startswith('```'):
        t = t.split('```')[1]
        if t.startswith('json'): t = t[4:]
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i: t = t[i:j+1]
    return json.loads(t)

ANALYZE = """你是短视频内容分析师。下面是真实短视频转录稿（可能有少量识别错字，请按语义理解）。

标题：{title}
转录全文：
{text}

输出严格 JSON（不要 markdown 围栏）：
{{
 "summary": "80字内中文摘要：讲了什么、对谁有用、核心价值",
 "category": "从这些里选一个：知识科普/观点评论/带货种草/教程技能/测评探店/日常vlog/影视娱乐",
 "keywords": ["4-6个关键词"],
 "hook": "开头3秒怎么抓人，一句话",
 "audience": "这条内容最想打动谁",
 "highlights": ["3-5个最有信息量的点，每条不超过25字"]
}}"""

BREAKDOWN = """你是爆款拆解专家。下面是一条真实短视频的转录稿与它的数据。

标题：{title}
平台：{platform}
播放 {play} / 点赞 {like} / 评论 {comment} / 收藏 {favorite}

转录全文：
{text}

输出严格 JSON（不要 markdown 围栏）：
{{
 "verdict": "这条为什么能爆，一句话结论",
 "hook_type": "开头钩子类型：悬念/利益点/反常识/情绪共鸣/身份认同/视觉冲击",
 "structure": [{{"stage":"开场/展开/高潮/收尾","what":"这一步做了什么","why":"为什么有效"}}],
 "viral_factors": ["3-5个可复用的爆款要素"],
 "reusable_formula": "一句话可复用的公式",
 "risk": "跟做时的注意事项，一句话"
}}"""

def main():
    tr = json.load(open(SRC, encoding='utf-8'))
    usable = {k:v for k,v in tr.items() if v.get('chars',0) >= 80}
    print(f'usable transcripts: {len(usable)}')
    out = {}
    for i,(path,v) in enumerate(usable.items(),1):
        title = Path(path).stem
        text = v['full_text']
        rec = {'path': path, 'duration': v['duration'], 'chars': v['chars'], 'full_text': text}
        try:
            t0=time.time()
            a = parse_json(llm(ANALYZE.format(title=title, text=text[:6000])))
            rec['analysis'] = a
            b = parse_json(llm(BREAKDOWN.format(title=title, platform='douyin',
                            play=100000, like=5000, comment=300, favorite=800, text=text[:6000])))
            rec['breakdown'] = b
            print(f"[{i}/{len(usable)}] {time.time()-t0:5.1f}s  {a.get('category','?'):8s} {title[:34]}")
        except Exception as e:
            rec['error'] = str(e)
            print(f'[{i}/{len(usable)}] FAIL {title[:30]}: {e}')
        out[path] = rec
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), 'utf-8')
    print('wrote', OUT)

main()
