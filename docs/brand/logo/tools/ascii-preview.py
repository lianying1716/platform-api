"""渲染 SVG → 白底灰度 → ASCII，用于无图像能力时"看"形状。"""
import io, sys, os
import cairosvg
from PIL import Image

CH = "@%#*+=-:. "   # 最暗 → 最亮

def show(path, w=48):
    png = cairosvg.svg2png(url=path, output_width=w*3, output_height=w*3)
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    im = Image.alpha_composite(bg, im).convert("L")
    im = im.resize((w, w), Image.LANCZOS)
    px = im.load()
    print(f"\n=== {os.path.basename(path)} ===")
    for y in range(im.height):
        print("".join(CH[min(9, px[x, y] * 10 // 256)] for x in range(im.width)))

for p in sys.argv[1:]:
    show(p)
