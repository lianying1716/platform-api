"""星采 Logo 概念稿 v4：几何边角全部锁定在 15°，轻量图标化。"""
import math, os
OUT = "/path/to/xsai-workbench/platform-api/docs/brand/logo/concepts"
C, R = 128.0, 118.0

def poly(pts):
    return "M " + " L ".join(f"{round(x,2)} {round(y,2)}" for x, y in pts) + " Z"

def pt(a_deg, r, cx=C, cy=C):
    a = math.radians(a_deg)
    return (cx + r * math.cos(a), cy + r * math.sin(a))

def rot(p, deg):
    a = math.radians(deg); x, y = p[0] - C, p[1] - C
    return (C + x * math.cos(a) - y * math.sin(a), C + x * math.sin(a) + y * math.cos(a))

def svg(body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" '
            f'role="img" aria-labelledby="t">\n  <title id="t">{title}</title>\n{body}\n</svg>\n')

def write(name, body, title):
    open(os.path.join(OUT, name), "w", encoding="utf-8").write(svg(body, title))
    print("wrote", name)

def ri_for(R_, theta):
    """内顶点固定在 45° 对角线上时，让边与水平成 theta 角所需的内半径。"""
    return R_ / (0.70710678 * (1 + 1 / math.tan(math.radians(theta))))

def star(R_, theta, start=-90):
    ri = ri_for(R_, theta)
    pts = []
    for i in range(8):
        a = start + i * 45
        pts.append(pt(a, R_ if i % 2 == 0 else ri))
    return poly(pts)

def blades(R_, theta, half_gap, start=-90):
    """4 片刀锋：内顶点从 45° 收进 half_gap，边仍锁定 theta 角。"""
    inner_a = 45 - half_gap
    # 由边角 theta 反解内半径
    ri = R_ * math.tan(math.radians(theta)) / (
        math.sin(math.radians(inner_a)) + math.cos(math.radians(inner_a)) * math.tan(math.radians(theta)))
    out = []
    for k in range(4):
        axis = start + k * 90
        apex = pt(axis, R_)
        p1 = pt(axis + inner_a, ri)
        p2 = pt(axis - inner_a, ri)
        out.append(f'  <path fill="#000" d="{poly([apex, p1, p2])}"/>')
    return "\n".join(out)

# A 聚合刀锋：4 片汇聚成星，片间留缝
write("a-blade.svg", blades(R, 15, 5, start=0), "Blade Star")

# B 实心四角星（15°，轻）
write("b-star.svg", f'  <path fill="#000" d="{star(R, 15)}"/>', "Solid Star")

# C 取景框 + 星
ARMS, TH, O = 58.0, 26.0, 28.0
br = [(O, O), (O + ARMS, O), (O + ARMS, O + TH), (O + TH, O + TH), (O + TH, O + ARMS), (O, O + ARMS)]
brk = "\n".join(f'  <path fill="#000" d="{poly([rot(p, 90*k) for p in br])}"/>' for k in range(4))
write("c-finder.svg", brk + f'\n  <path fill="#000" d="{star(56, 15)}"/>', "Finder Star")
print("done")
