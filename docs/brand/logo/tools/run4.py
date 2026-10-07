import json, os, subprocess, sys, time
sys.path.insert(0, "/path/to/xsai-workbench/logo-gen")
from prompts4 import COMMON, PROMPTS
OUT = "/path/to/xsai-workbench/logo-gen/raw4"
os.makedirs(OUT, exist_ok=True)
KEY = open("/path/to/api-key").read().strip()
API = "https://api.xsai5.xyz/v1/images/generations"
def gen(out, prompt, model="gpt-image-2.5", size="1024x1024"):
    body = json.dumps({"model": model, "prompt": prompt, "size": size, "n": 1})
    r = subprocess.run(["curl","-s","-m","300",API,"-H",f"Authorization: Bearer {KEY}",
        "-H","Content-Type: application/json","-d",body], capture_output=True, text=True)
    try: d = json.loads(r.stdout)
    except Exception: print("  !! bad json", r.stdout[:160]); return False
    if "error" in d: print("  !! api", json.dumps(d["error"],ensure_ascii=False)[:180]); return False
    url = d["data"][0].get("url")
    if not url: print("  !! no url"); return False
    subprocess.run(["curl","-s","-m","300","-o",out,url], check=True); return True
for name in (sys.argv[1:] or list(PROMPTS)):
    full = f"Use case: logo-brand. Asset type: logo concept. Primary request: {PROMPTS[name]}. Style/medium: {COMMON}"
    t0=time.time(); ok = gen(os.path.join(OUT, f"{name}.png"), full)
    print(f"{'✓' if ok else '✗'} {name} {time.time()-t0:.0f}s")
