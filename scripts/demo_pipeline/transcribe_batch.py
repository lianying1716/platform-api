"""批量转录 demo_storage 里的真实视频（跳过超大/占位），写 JSON 结果。"""
import json, os, sys, time, subprocess
from pathlib import Path

# 相对本仓库根目录运行（或设 PLATFORM_ROOT / DEMO_BUILD_DIR 覆盖）
ROOT = Path(os.environ.get('PLATFORM_ROOT', Path(__file__).resolve().parents[2]))
STORAGE = Path(os.environ.get('DEMO_STORAGE', ROOT / 'data/demo_storage'))
BUILD_DIR = Path(os.environ.get('DEMO_BUILD_DIR', ROOT / 'data/demo_pipeline'))
OUT = BUILD_DIR / 'transcripts.json'
BUILD_DIR.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 60 * 1024 * 1024       # 跳过超大（>60MB）
MIN_BYTES = 500 * 1024             # 跳过 3 秒占位（31KB）
MAX_DUR = 300                      # 跳过超长（>5分钟）

def probe(p):
    try:
        d = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                            '-of','csv=p=0',str(p)],capture_output=True,text=True,timeout=30)
        return float(d.stdout.strip() or 0)
    except Exception:
        return 0.0

def main():
    from faster_whisper import WhisperModel
    cands=[]
    for p in STORAGE.rglob('*.mp4'):
        if '/demo/' in str(p):        # 占位素材，跳过
            continue
        sz=p.stat().st_size
        if sz<MIN_BYTES or sz>MAX_BYTES: continue
        dur=probe(p)
        if dur<8 or dur>MAX_DUR: continue   # 太短/太长都跳过
        cands.append((str(p),sz,dur))
    cands.sort(key=lambda x:-x[1])
    print(f'candidates: {len(cands)}')
    import os
    m=WhisperModel('small',device='cpu',compute_type='int8',cpu_threads=max(4,os.cpu_count()//2))
    out={}
    for i,(p,sz,dur) in enumerate(cands,1):
        t0=time.time()
        try:
            segs,info=m.transcribe(p,language='zh',vad_filter=True,beam_size=5,condition_on_previous_text=False)
            segs=list(segs)
            text=''.join(s.text for s in segs).strip()
            out[p]={'duration':round(dur,2),'size':sz,'language':info.language,
                    'chars':len(text),'secs':round(time.time()-t0,1),
                    'segments':[{'t':round(s.start,2),'text':s.text.strip()} for s in segs],
                    'full_text':text}
            print(f'[{i}/{len(cands)}] {dur:6.1f}s chars={len(text):4d} {time.time()-t0:5.1f}s  {Path(p).name[:34]}')
        except Exception as e:
            print(f'[{i}/{len(cands)}] FAIL {Path(p).name}: {e}')
            out[p]={'error':str(e),'duration':dur,'size':sz}
        OUT.write_text(json.dumps(out,ensure_ascii=False,indent=1),'utf-8')
    print('wrote',OUT)
main()
