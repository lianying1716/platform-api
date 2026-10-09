import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prompts9 import PROMPTS, build
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw9")
os.makedirs(OUT, exist_ok=True)
KEY = open("/path/to/api-key").read().strip()
API = "https://api.xsai5.xyz/v1/images/generations"
MODEL = os.environ.get("IMG_MODEL", "gpt-image-2.5")
SIZE = os.environ.get("IMG_SIZE", "1024x1024")

def gen(out, prompt):
    body = json.dumps({"model": MODEL, "prompt": prompt, "size": SIZE, "n": 1})
    r = subprocess.run(["curl","-s","-m","300",API,"-H",f"Authorization: Bearer {KEY}",
        "-H","Content-Type: application/json","-d",body], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except Exception:
        print("  !! bad json", r.stdout[:200]); return False
    if "error" in d:
        print("  !! api", json.dumps(d["error"], ensure_ascii=False)[:220]); return False
    url = d["data"][0].get("url")
    if not url:
        b64 = d["data"][0].get("b64_json")
        if b64:
            import base64; open(out,"wb").write(base64.b64decode(b64)); return True
        print("  !! no url"); return False
    subprocess.run(["curl","-s","-m","300","-o",out,url], check=True); return True

targets = sys.argv[1:] or list(PROMPTS)
for name in targets:
    t0 = time.time()
    ok = gen(os.path.join(OUT, f"{name}.png"), build(name))
    print(f"{'OK ' if ok else 'FAIL'} {name} {time.time()-t0:.0f}s", flush=True)
