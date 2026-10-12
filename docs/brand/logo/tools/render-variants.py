"""把概念稿换成包豪斯配色，并渲染多种尺寸的 PNG。"""
import os, subprocess, sys
CON = "/path/to/xsai-workbench/platform-api/docs/brand/logo/concepts"
REN = "/path/to/xsai-workbench/platform-api/docs/brand/logo/renders"
os.makedirs(REN, exist_ok=True)
INK, ACCENT, BG = "#1A1512", "#CC2823", "#F3F0E7"

def recolor(src, dst, color):
    s = open(src, encoding="utf-8").read()
    s = s.replace('fill="#000"', f'fill="{color}"').replace('stroke="#000"', f'stroke="{color}"')
    open(dst, "w", encoding="utf-8").write(s)

def render(svg, png, size):
    import cairosvg
    cairosvg.svg2png(url=svg, write_to=png, output_width=size, output_height=size)

concepts = [
    ("a-blade", "A 聚合刀锋"), ("b-star", "B 实心四角星"), ("c-finder", "C 取景框+星"),
    ("d-inward", "D 内向箭头"), ("e-star-hole", "E 星+方孔"), ("f-finder2", "F 轻取景框"),
]
for slug, label in concepts:
    src = os.path.join(CON, f"{slug}.svg")
    acc = os.path.join(CON, f"{slug}-accent.svg")
    recolor(src, acc, ACCENT)
    for size in (512, 128, 32):
        render(acc, os.path.join(REN, f"{slug}-{size}.png"), size)
    print("rendered", slug)
print("done")
