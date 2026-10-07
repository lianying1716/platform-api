"""画廊 v2：图片落成独立文件，HTML 只做引用（体积小、可缓存）。"""
import os, shutil
from PIL import Image

ROOT = "/path/to/xsai-workbench/logo-gen"
DST = "/path/to/platform-api/web/public/brand"
os.makedirs(DST, exist_ok=True)

ITEMS = [
 ("raw2/14-diamond-cut.png", "D", "菱切", "大菱形被一条竖缝切成左右两块，不对称。评审唯一评为「不像现成模板」的一款。", 7.5,
  "16px 实测：能辨认，读作「钻石 / 菱形」"),
 ("raw4/32-negative-star.png", "H", "负空间星", "实心方块里挖出四角星，星的下角刺破底边。", 7.0,
  "16px 实测：能辨认，读作「准星」，黑白对比最强"),
 ("raw/02-finder-star.png", "B", "取景框", "四角取景框锁定中心星。工具属性最明确。", 7.0,
  "16px 实测：能辨认，读作「对焦 / 取景框」"),
 ("raw3/23-cai-tree.png", "L", "采树", "从「采」字结构取形，人形 / 树形剪影。", 6.0,
  "结构干净，但有点像通用「小人」图标"),
 ("raw3/24-gather-notch.png", "M", "缺口方块", "粗方框缺一角，菱形悬在缺口外。", 6.0,
  "靠近 UI「裁剪 / 添加」类图标"),
 ("raw3/25-funnel-cut.png", "N", "漏斗", "倒梯形漏斗 + 下方方块，物料流出的意象。", 6.0,
  "轮廓接近通用 filter 图标"),
 ("raw4/36-three-point.png", "P", "三芒", "三个不等长三角刃 + 六边形核心，非常规星形。", 6.0,
  "旋转对称需再校准"),
 ("raw2/13-fold-corner.png", "F", "折角", "方框 + 右下实心块 + 左上悬浮菱形。", 6.0,
  "左上菱形缩小后会变噪点"),
 ("raw/01-collect-star.png", "A", "聚合箭头", "四支粗箭头向中心汇聚，中间留出星形。", 6.0,
  "「箭头汇聚」是最通用的系统图标母题"),
 ("raw4/31-shards-to-star.png", "G", "碎片成星", "五个不等大色块聚成一颗不完全对称的星。", 5.0,
  "圆角处理不统一，母形仍像通用星芒"),
 ("raw/05-orbit-star.png", "E", "轨道", "四角星被一条斜环穿插。", 5.0,
  "穿插处缩小会糊"),
 ("raw3/21-cai-glyph.png", "K", "采字符", "把「采」抽象成几何笔画。", 5.0,
  "斜线间距太窄，16px 会粘连"),
 ("raw4/33-grasp-blocks.png", "I", "抓取", "三根斜指 + 两个方块。", 5.0,
  "斜线粘连，重心偏左"),
 ("raw3/26-pick-grid.png", "O", "九宫格", "3×3 网格，正中一格填实。", 5.0,
  "缩下去塌成纹理"),
 ("raw/03-inbox-star.png", "C", "托盘", "星 + 托举容器 + 上方竖条。", 4.0,
  "元素太多，16px 会散架"),
 ("raw/06-star-shard.png", "Q", "星片", "四块拼成的星，中间十字缝。", 4.0,
  "缝隙糊死"),
 ("raw2/12-layer-lift.png", "R", "叠层", "阶梯状水平条堆叠。", 4.0,
  "像通用「列表 / 进度条」图标"),
 ("raw2/11-harvest-comb.png", "S", "梳齿", "五根竖条顶部斜切。", 4.0,
  "像柱状图，且有「下行」负语义"),
 ("raw4/35-overflow-box.png", "T", "溢出", "方块从容器口升起。", 4.0,
  "像通用「下载 / 漏斗」图标"),
 ("raw3/22-cai-hand.png", "U", "采手", "横杠下挂四斜线 + 竖杆方点。", 4.0,
  "粗细不统一，重心偏右"),
]

def export(src, dst_base):
    """导出大图 + 64/32/16 缩放 + 反白版，返回路径字典。"""
    im = Image.open(os.path.join(ROOT, src)).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255,255,255,255))
    flat = Image.alpha_composite(bg, im).convert("RGB")
    out = {}
    for tag, size in (("big",320), ("s64",64), ("s32",32), ("s16",16)):
        c = flat.copy(); c.thumbnail((size,size), Image.LANCZOS)
        p = f"{dst_base}-{tag}.png"; c.save(os.path.join(DST,p), optimize=True)
        out[tag] = p
    inv = Image.new("RGB", (56,56), (26,21,18))
    c = flat.copy(); c.thumbnail((44,44), Image.LANCZOS)
    inv.paste(c, ((56-c.width)//2, (56-c.height)//2))
    p = f"{dst_base}-inv.png"; inv.save(os.path.join(DST,p))
    out["inv"] = p
    return out

cards = []
for src, letter, name, desc, score, note in ITEMS:
    base = f"c{letter.lower()}"
    u = export(src, base)
    cards.append(f'''
  <article class="card{' top' if score>=7 else ''}">
    <div class="art"><img src="/brand/{u['big']}" alt="{letter} {name}" loading="lazy"></div>
    <div class="row"><span class="letter">{letter}</span><h2>{name}</h2><span class="score">{score}</span></div>
    <p class="desc">{desc}</p>
    <p class="note">{note}</p>
    <div class="ladder">
      <figure><img src="/brand/{u['s64']}"><figcaption>64</figcaption></figure>
      <figure><img src="/brand/{u['s32']}"><figcaption>32</figcaption></figure>
      <figure><img src="/brand/{u['s16']}"><figcaption>16</figcaption></figure>
      <figure class="inv"><img src="/brand/{u['inv']}"><figcaption>反白</figcaption></figure>
    </div>
  </article>''')

html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>星采 · logo 候选画廊</title><style>
:root {{ --ink:#1A1512; --accent:#CC2823; --bg:#F3F0E7; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:36px; background:var(--bg); color:var(--ink);
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif; }}
header {{ border-bottom:3px solid var(--ink); padding-bottom:16px; margin-bottom:28px; }}
h1 {{ margin:0 0 8px; font-size:32px; letter-spacing:-.01em; }}
.sub {{ color:#6b6257; font-size:14px; line-height:1.7; }}
.sub b {{ color:var(--accent); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); gap:18px; }}
.card {{ background:#fff; border:2px solid var(--ink); box-shadow:4px 4px 0 var(--ink); padding:18px; }}
.card.top {{ box-shadow:6px 6px 0 var(--accent); border-color:var(--accent); }}
.art {{ display:grid; place-items:center; padding:4px 0 10px; min-height:172px; }}
.art img {{ width:150px; height:150px; object-fit:contain; }}
.row {{ display:flex; align-items:center; gap:8px; }}
.letter {{ display:grid; place-items:center; width:22px; height:22px; background:var(--ink);
  color:#fff; font-size:12px; font-weight:800; }}
h2 {{ margin:0; font-size:16px; }}
.score {{ margin-left:auto; font-weight:800; font-size:15px; color:var(--accent); }}
.desc {{ margin:8px 0 4px; font-size:13px; line-height:1.55; color:#4b463e; }}
.note {{ margin:0 0 12px; font-size:12px; line-height:1.5; color:#8a8177; }}
.ladder {{ display:flex; align-items:flex-end; gap:14px; border-top:1px solid #e4ddd0; padding-top:10px; }}
.ladder figure {{ margin:0; text-align:center; }}
.ladder img {{ display:block; margin:0 auto 3px; }}
.ladder figcaption {{ font-size:10px; color:#8a8177; }}
.ladder .inv {{ margin-left:auto; }}
</style></head><body>
<header>
  <h1>星采 · logo 候选画廊</h1>
  <div class="sub">
    共 {len(ITEMS)} 款，按视觉模型评审得分排序。<b>红框是评分最高的三款</b>。<br>
    每张都带 64 / 32 / 16 px 缩放和深底反白测试 —— 请重点看 <b>16px</b> 那格还能不能认出来。<br>
    评审只描述事实与问题，<b>好不好看最终由你定</b>。
  </div>
</header>
<div class="grid">{''.join(cards)}</div>
<p style="margin-top:28px;color:#6b6257;font-size:13px;line-height:1.7">
  看中哪款，把它的编号（如 <b>D 菱切</b>）告诉我。<br>
  我会把它做成正式母版：矢量重绘成精确 SVG、透明背景、favicon / app icon / 单色 / 反白全套，再接进产品。
</p>
</body></html>'''
open(os.path.join(DST, "logo-gallery.html"), "w", encoding="utf-8").write(html)
n = len([f for f in os.listdir(DST) if f.endswith('.png')])
print(f"画廊已生成，引用 {n} 个图片文件")
