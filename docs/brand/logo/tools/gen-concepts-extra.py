"""v5：补两个"采集"语义更强的方向。"""
import math, os
OUT = "/path/to/platform-api/docs/brand/logo/concepts"
C = 128.0

def poly(pts):
    return "M " + " L ".join(f"{round(x,2)} {round(y,2)}" for x, y in pts) + " Z"
def pt(a, r, cx=C, cy=C):
    a = math.radians(a); return (cx + r*math.cos(a), cy + r*math.sin(a))
def svg(body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" '
            f'role="img" aria-labelledby="t">\n  <title id="t">{title}</title>\n{body}\n</svg>\n')
def write(n, b, t):
    open(os.path.join(OUT, n), "w", encoding="utf-8").write(svg(b, t)); print("wrote", n)

def ri_for(R_, theta):
    return R_ / (0.70710678 * (1 + 1 / math.tan(math.radians(theta))))
def star(R_, theta, start=-90):
    ri = ri_for(R_, theta); pts = []
    for i in range(8):
        pts.append(pt(start + i*45, R_ if i % 2 == 0 else ri))
    return poly(pts)

# D. 内聚：4 枚箭头指向中心的小星（"采集"= 收拢进来）
def inward(R_outer=120.0, R_tip=56.0, half=15.0, thick=30.0):
    out = []
    for k in range(4):
        axis = -90 + k*90
        tip = pt(axis, R_tip)
        # 箭头两翼
        w1 = pt(axis + half, R_tip + thick)
        w2 = pt(axis - half, R_tip + thick)
        # 箭杆两端（切向加宽）
        s1 = pt(axis + 30, R_tip + thick + 46)
        s2 = pt(axis - 30, R_tip + thick + 46)
        out.append(f'  <path fill="#000" d="{poly([tip, w1, s1, s2, w2])}"/>')
    return "\n".join(out)
write("d-inward.svg", inward() + f'\n  <path fill="#000" d="{star(30, 15)}"/>', "Inward Collect")

# E. 星中留方孔（负空间），更轻
def star_hole(R_, theta, hole):
    ri = ri_for(R_, theta); pts = []
    for i in range(8):
        pts.append(pt(-90 + i*45, R_ if i % 2 == 0 else ri))
    outer = poly(pts)
    h = poly([(C-hole, C-hole), (C+hole, C-hole), (C+hole, C+hole), (C-hole, C+hole)])
    return outer + " " + h
write("e-star-hole.svg", f'  <path fill="#000" fill-rule="evenodd" d="{star_hole(118, 18, 26)}"/>',
      "Star with Centre Hole")

# F. 取景框更轻 + 大星
ARMS, TH, O = 62.0, 22.0, 26.0
def rot(p, deg):
    a = math.radians(deg); x, y = p[0]-C, p[1]-C
    return (C + x*math.cos(a) - y*math.sin(a), C + x*math.sin(a) + y*math.cos(a))
br = [(O,O), (O+ARMS,O), (O+ARMS,O+TH), (O+TH,O+TH), (O+TH,O+ARMS), (O,O+ARMS)]
brk = "\n".join(f'  <path fill="#000" d="{poly([rot(p, 90*k) for p in br])}"/>' for k in range(4))
write("f-finder2.svg", brk + f'\n  <path fill="#000" d="{star(64, 15)}"/>', "Finder Star Light")
print("done")
