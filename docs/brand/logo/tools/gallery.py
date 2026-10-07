"""生成选稿画廊：按评审分排序，展示大图 + 16/32px 缩放 + 深底反白测试。"""
import base64, io, os
from PIL import Image

ROOT = "/path/to/xsai-workbench/logo-gen"
OUT = "/path/to/platform-api/app/static/brand/logo-gallery.html"

# (文件, 名称, 概念说明, 评审分, 评审要点)
ITEMS = [
 ("raw2/14-diamond-cut.png", "D 菱切", "大菱形被一条竖缝切成左右两块，不对称。评审唯一评为「不像现成模板」的一款。", 7.5,
  "16px 强；建议微调缝宽、加一点倒角提升高级感"),
 ("raw4/32-negative-star.png", "H 负空间星", "实心方块里挖出四角星，星的下角刺破底边。", 7.0,
  "16px 强；注意底角断开在缩小后是否会糊"),
 ("raw/02-finder-star.png", "B 取景框", "四角取景框锁定中心星。工具属性最明确。", 7.0,
  "16px 强；但「取景框+星」是 AI/素材类最常见的符号"),
 ("raw3/23-cai-tree.png", "L 采树", "从「采」字结构取形，人形/树形剪影。", 6.0,
  "16px 中；结构干净，但有点像通用「小人」图标"),
 ("raw3/24-gather-notch.png", "M 缺口方块", "粗方框缺一角，菱形悬在缺口外。", 6.0,
  "16px 中；靠近 UI「裁剪/添加」类图标"),
 ("raw3/25-funnel-cut.png", "N 漏斗", "倒梯形漏斗 + 下方方块，物料流出的意象。", 6.0,
  "16px 中；轮廓接近通用 filter 图标"),
 ("raw4/36-three-point.png", "P 三芒", "三个不等长三角刃 + 六边形核心，非常规星形。", 6.0,
  "16px 中；旋转对称需再校准"),
 ("raw2/13-fold-corner.png", "F 折角", "方框 + 右下实心块 + 左上悬浮菱形。", 6.0,
  "16px 中；左上菱形缩小会变噪点"),
 ("raw/01-collect-star.png", "A 聚合箭头", "四支粗箭头向中心汇聚，中间留出星形。", 6.0,
  "16px 强，但「箭头汇聚」是最通用的系统图标母题"),
 ("raw4/31-shards-to-star.png", "G 碎片成星", "五个不等大色块聚成一颗不完全对称的星。", 5.0,
  "圆角处理不统一，母形仍像通用星芒"),
 ("raw/05-orbit-star.png", "E 轨道", "四角星被一条斜环穿插。", 5.0,
  "16px 弱，穿插处缩小会糊"),
 ("raw3/21-cai-glyph.png", "K 采字符", "把「采」抽象成几何笔画。", 5.0,
  "16px 弱，斜线间距太窄"),
 ("raw4/33-grasp-blocks.png", "I 抓取", "三根斜指 + 两个方块。", 5.0,
  "16px 弱，斜线粘连"),
 ("raw3/26-pick-grid.png", "O 九宫格", "3×3 网格，正中一格填实。", 5.0,
  "16px 弱，缩下去塌成纹理"),
 ("raw/03-inbox-star.png", "C 托盘", "星 + 托举容器 + 上方竖条。", 4.0,
  "元素太多，16px 会散架"),
 ("raw/06-star-shard.png", "F2 星片", "四块拼成的星，中间十字缝。", 4.0,
  "16px 弱，缝隙糊死"),
 ("raw2/12-layer-lift.png", "E2 叠层", "阶梯状水平条堆叠。", 4.0,
  "像通用「列表/进度条」图标"),
 ("raw2/11-harvest-comb.png", "D2 梳齿", "五根竖条顶部斜切。", 4.0,
  "像柱状图，且有「下行」负语义"),
 ("raw4/35-overflow-box.png", "J 溢出", "方块从容器口升起。", 4.0,
  "像通用「下载/漏斗」图标"),
 ("raw3/22-cai-hand.png", "K2 采手", "横杠下挂四斜线 + 竖杆方点。", 4.0,
  "粗细不统一，重心偏右"),
]

def uri(path, size, invert=False):
    im = Image.open(os.path.join(ROOT, path)).convert("RGBA")
    im.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (26,21,18,255) if invert else (255,255,255,0))
    canvas.paste(im, ((size-im.width)//2, (size-im.height)//2), im)
    buf = io.BytesIO(); canvas.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

cards = []
for path, name, desc, score, note in ITEMS:
    cards.append(f'''
  <article class="card{' top' if score>=7 else ''}">
    <div class="art"><img src="{uri(path, 260)}" alt="{name}"></div>
    <div class="row"><h2>{name}</h2><span class="score">{score}</span></div>
    <p class="desc">{desc}</p>
    <p class="note">{note}</p>
    <div class="ladder">
      <figure><img src="{uri(path,64)}"><figcaption>64</figcaption></figure>
      <figure><img src="{uri(path,32)}"><figcaption>32</figcaption></figure>
      <figure><img src="{uri(path,16)}"><figcaption>16</figcaption></figure>
      <figure class="inv"><img src="{uri(path,56,True)}"><figcaption>反白</figcaption></figure>
    </div>
  </article>''')

html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>星采 · logo 候选画廊</title><style>
:root {{ --ink:#1A1512; --accent:#CC2823; --bg:#F3F0E7; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:36px; background:var(--bg); color:var(--ink);
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif; }}
header {{ border-bottom:3px solid var(--ink); padding-bottom:16px; margin-bottom:28px; }}
h1 {{ margin:0 0 6px; font-size:32px; letter-spacing:-.01em; }}
.sub {{ color:#6b6257; font-size:14px; line-height:1.6; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); gap:18px; }}
.card {{ background:#fff; border:2px solid var(--ink); box-shadow:4px 4px 0 var(--ink); padding:18px; }}
.card.top {{ box-shadow:6px 6px 0 var(--accent); border-color:var(--accent); }}
.art {{ display:grid; place-items:center; padding:6px 0 10px; min-height:170px; }}
.art img {{ width:150px; height:150px; object-fit:contain; }}
.row {{ display:flex; align-items:baseline; gap:8px; }}
h2 {{ margin:0; font-size:16px; }}
.score {{ margin-left:auto; font-weight:800; font-size:15px; color:var(--accent); }}
.desc {{ margin:8px 0 4px; font-size:13px; line-height:1.55; color:#4b463e; }}
.note {{ margin:0 0 12px; font-size:12px; line-height:1.5; color:#8a8177; }}
.ladder {{ display:flex; align-items:flex-end; gap:14px; border-top:1px solid #e4ddd0; padding-top:10px; }}
.ladder figure {{ margin:0; text-align:center; }}
.ladder img {{ display:block; margin:0 auto 3px; }}
.ladder figcaption {{ font-size:10px; color:#8a8177; }}
.ladder .inv {{ margin-left:auto; background:var(--ink); padding:5px 7px 1px; }}
.ladder .inv figcaption {{ color:#b9b1a6; }}
</style></head><body>
<header>
  <h1>星采 · logo 候选画廊</h1>
  <div class="sub">
    共 20 款，按视觉模型评审得分排序。<b>红框是评分最高的三款</b>。<br>
    每张都带 64 / 32 / 16 px 缩放和深底反白测试 —— 请重点看 16px 那格还能不能认出来。<br>
    评审只描述事实与问题，<b>好不好看最终由你定</b>。
  </div>
</header>
<div class="grid">{''.join(cards)}</div>
<p style="margin-top:28px;color:#6b6257;font-size:13px">
  看中哪款，把它的编号（如 D 菱切 / H 负空间星）告诉我。我会把它做成正式母版：
  矢量重绘、透明背景、favicon/app icon 全套、单色与反白变体，再接进产品。
</p>
</body></html>'''
open(OUT,"w",encoding="utf-8").write(html)
print("wrote", OUT, os.path.getsize(OUT))
