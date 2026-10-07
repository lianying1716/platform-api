"""把生成的图发给带视觉的模型，让它描述/评审 —— 无图像能力时的"眼睛"。"""
import base64, json, subprocess, sys, os, tempfile
KEY = open("/path/to/api-key").read().strip()
API = "https://api.xsai5.xyz/v1/chat/completions"

def ask(img_path, question, model="qwen3.8-flash", max_side=768):
    from PIL import Image
    im = Image.open(img_path).convert("RGB")
    if max(im.size) > max_side:                      # 缩图，避免请求体过大
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    im.save(tmp.name, "JPEG", quality=88)
    b64 = base64.b64encode(open(tmp.name, "rb").read()).decode()
    os.unlink(tmp.name)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
        "max_tokens": 800,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh); body = fh.name
    out = subprocess.run(["curl","-s","-m","240",API,
        "-H",f"Authorization: Bearer {KEY}","-H","Content-Type: application/json",
        "--data-binary",f"@{body}"], capture_output=True, text=True)
    os.unlink(body)
    try:
        d = json.loads(out.stdout)
        if "error" in d:
            return "ERR: " + json.dumps(d["error"], ensure_ascii=False)[:300]
        return d["choices"][0]["message"]["content"]
    except Exception as e:
        return f"PARSE-ERR {e}: {out.stdout[:300]}"

if __name__ == "__main__":
    p = sys.argv[1]
    q = sys.argv[2] if len(sys.argv) > 2 else "用中文简短描述这张图：主体是什么形状？什么颜色？背景？只描述事实。"
    print(ask(p, q))
