import os, base64, cairosvg
CON = "/path/to/xsai-workbench/platform-api/docs/brand/logo/concepts"
OUT = "/path/to/xsai-workbench/platform-api/app/static/brand/logo-preview.html"
ACCENT, INK, BG, BLUE = "#CC2823", "#1A1512", "#F3F0E7", "#0B58BB"

concepts = [
    ("a-blade", "A · 聚合刀锋", "四片刀锋向中心汇聚成星。采集的动作感最强，片间留缝让它不闷。"),
    ("b-star", "B · 实心四角星", "最简、最满幅，纯几何。16px 下依然认得，缩到 favicon 也不糊。"),
    ("c-finder", "C · 取景框 + 星", "取景框锁定中心星：采集 + 聚焦，工具属性最明确。"),
    ("d-inward", "D · 内向箭头", "四枚箭头指向中心小星。语义最直白，但细节最多。"),
    ("e-star-hole", "E · 星 + 方孔", "星形中央留方孔，最轻、留白最多，最安静的一款。"),
    ("f-finder2", "F · 轻取景框", "取景框更细、星更大。比 C 轻盈，边框感仍在。"),
]

def data_uri(svg_path, color, size=220):
    s = open(svg_path, encoding="utf-8").read()
    s = s.replace('fill="#000"', f'fill="{color}"').replace('stroke="#000"', f'stroke="{color}"')
    tmp = "/tmp/_pv.svg"
    open(tmp, "w", encoding="utf-8").write(s)
    png = cairosvg.svg2png(url=tmp, output_width=size, output_height=size)
    return "data:image/png;base64," + base64.b64encode(png).decode()

cards = []
for slug, name, note in concepts:
    p = os.path.join(CON, f"{slug}.svg")
    big = data_uri(p, ACCENT, 220)
    ink = data_uri(p, INK, 220)
    s64 = data_uri(p, INK, 64); s32 = data_uri(p, INK, 32); s16 = data_uri(p, INK, 16)
    cards.append(f'''
    <article class="card">
      <div class="art"><img src="{big}" alt="{name}"></div>
      <h2>{name}</h2>
      <p class="note">{note}</p>
      <div class="scale">
        <figure><img src="{s64}"><figcaption>64</figcaption></figure>
        <figure><img src="{s32}"><figcaption>32</figcaption></figure>
        <figure><img src="{s16}"><figcaption>16</figcaption></figure>
        <figure class="onink"><img src="{ink}"><figcaption>单色</figcaption></figure>
      </div>
    </article>''')

html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>星采 Stargather · logo 概念</title>
<style>
  :root {{ --ink:{INK}; --accent:{ACCENT}; --bg:{BG}; --blue:{BLUE}; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:40px; background:var(--bg); color:var(--ink);
         font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif; }}
  header {{ border-bottom:3px solid var(--ink); padding-bottom:18px; margin-bottom:32px; }}
  h1 {{ margin:0 0 6px; font-size:34px; letter-spacing:-.01em; }}
  .sub {{ color:#6b6257; font-size:14px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:20px; }}
  .card {{ background:#fff; border:2px solid var(--ink); box-shadow:5px 5px 0 var(--ink); padding:22px; }}
  .art {{ display:grid; place-items:center; padding:14px 0 6px; }}
  .art img {{ width:150px; height:150px; }}
  h2 {{ margin:10px 0 6px; font-size:17px; }}
  .note {{ margin:0 0 14px; font-size:13px; line-height:1.6; color:#5d564d; min-height:42px; }}
  .scale {{ display:flex; align-items:flex-end; gap:16px; border-top:1px solid #e4ddd0; padding-top:12px; }}
  .scale figure {{ margin:0; text-align:center; }}
  .scale img {{ display:block; margin:0 auto 4px; }}
  .scale figcaption {{ font-size:11px; color:#8a8177; }}
  .scale .onink {{ margin-left:auto; background:var(--ink); padding:6px 8px 2px; border-radius:0; }}
  .scale .onink figcaption {{ color:#b9b1a6; }}
  .scale .onink img {{ filter:invert(1) brightness(2); }}
</style></head>
<body>
<header>
  <h1>星采 · Stargather</h1>
  <div class="sub">自媒体创作的自动化素材管理加工 · logo 概念第一轮（包豪斯调性，砖红 #CC2823）</div>
</header>
<div class="grid">{''.join(cards)}</div>
<p style="margin-top:32px;color:#6b6257;font-size:13px">
  判定标准：16px 下还能认出来，才算能用。可以放大浏览器看 16px 那格。
</p>
</body></html>'''
open(OUT, "w", encoding="utf-8").write(html)
print("wrote", OUT, os.path.getsize(OUT), "bytes")
