# 第六轮（2026-10-09）：按 logo-design 简报（BRIEF.md）重做「星采」符号。
# 模型：星算 gpt-image-2.5（用户口中的 image2.5）。位图候选，选定后重绘为 SVG 母版。
#
# 三条互不相同的方向（见 BRIEF.md「Mark type hypothesis」）：
#   A Converge  三向汇聚楔形（120°旋转对称）——「把散落的素材收拢」的动作
#   B Dock      实心方坯咬出一个圆口 + 一颗卫星圆点嵌进去——「内容入轨/靠泊」
#   C Ringslot  粗环带 + 三颗方形芯片按 120° 泊在环上——「平台收住所有账号/来源」
#
# 目标：包豪斯、硬边、零渐变、强剪影、16px 仍成立、只用品牌四色。

COMMON = (
 "flat vector logo mark, master-level geometric construction, solid flat shapes, "
 "exactly the colors described and nothing else, absolutely no gradient, no shading, no 3D, "
 "no outline, no stroke-only line, no texture, no mockup, no watermark, no text, no letters, "
 "crisp hard edges, bold heavy forms, strong simple silhouette that stays readable at 16 px, "
 "centered with generous even margin, single mark only, no four-point star, no magic wand, "
 "no robot, no arrow, no globe, no shutter blades"
)

PROMPTS = {
# ---- A · Converge · 三向汇聚 ------------------------------------------------
"a1-converge-red": (
 "an abstract geometric mark of exactly three identical thick tapered wedge blades, arranged in "
 "perfect 120 degree rotational symmetry around a common center, their wide ends on the outside and "
 "their points converging inward, leaving a small clean negative-space triangle at the very center; "
 "the whole mark reads as a heavy tri-radial funnel drawing pieces inward and as a spark of energy; "
 "all three wedges are one single flat color: vivid brick red (hex CC2823), "
 "on a plain warm off-white background (hex F3F0E7). Two colors total in the image"
),
"a2-converge-ink": (
 "an abstract geometric mark of exactly three identical thick tapered wedge blades, arranged in "
 "perfect 120 degree rotational symmetry around a common center, their wide ends on the outside and "
 "their points converging inward, leaving a small clean negative-space triangle at the very center; "
 "the whole mark reads as a heavy tri-radial funnel drawing pieces inward and as a spark of energy; "
 "all three wedges in warm near-black ink (hex 1A1512), with one tiny vivid brick red (hex CC2823) "
 "uniform square sitting exactly at the center point, "
 "on a plain warm off-white background (hex F3F0E7). Three flat colors total, no gradient"
),
# ---- B · Dock · 方坯咬口 + 卫星 --------------------------------------------
"b1-dock-red": (
 "an abstract geometric mark made of two shapes: a heavy solid square slab in warm near-black ink "
 "(hex 1A1512) with one large smooth quarter-circle bite cut out of its upper right corner, and a "
 "single thick solid vivid brick red (hex CC2823) ring or disc that nests snugly into that circular "
 "bite, touching it, like a satellite docking into an orbit cradle; "
 "the mark sits on a plain warm off-white background (hex F3F0E7). "
 "Exactly two shape colors plus the background, very heavy and blocky, strong outer silhouette"
),
"b2-dock-tricolor": (
 "an abstract geometric mark made of two shapes: a heavy solid square slab in strong cobalt blue "
 "(hex 0B58BB) with one large smooth quarter-circle bite cut out of its upper right corner, and a "
 "single thick solid vivid brick red (hex CC2823) ring that nests snugly into that circular bite, "
 "touching it, like a satellite docking into an orbit cradle; "
 "the mark sits on a plain warm off-white background (hex F3F0E7). "
 "Exactly two shape colors plus the background, very heavy and blocky, strong outer silhouette"
),
# ---- C · Ringslot · 粗环 + 三颗芯片 ----------------------------------------
"c1-ringslot-red": (
 "an abstract geometric mark: one very thick bold open circular ring in warm near-black ink "
 "(hex 1A1512), drawn as a solid annulus with a large clean hole, and exactly three identical small "
 "solid uniform squares in vivid brick red (hex CC2823) docked evenly on the outer edge of the ring "
 "at perfect 120 degree spacing like satellites locked onto an orbit; "
 "the squares are the only red, the ring is the only dark shape, "
 "on a plain warm off-white background (hex F3F0E7). Three flat colors total, no gradient"
),
"c2-ringslot-ink": (
 "an abstract geometric mark: one very thick bold open circular ring in vivid brick red "
 "(hex CC2823), drawn as a solid annulus with a large clean hole, and exactly three identical small "
 "solid uniform squares in warm near-black ink (hex 1A1512) docked evenly on the outer edge of the "
 "ring at perfect 120 degree spacing like satellites locked onto an orbit; "
 "on a plain warm off-white background (hex F3F0E7). Three flat colors total, no gradient"
),
}

def build(name):
    return (f"Use case: logo-brand. Asset type: logo mark for a web app favicon and sidebar. "
            f"Primary request: {PROMPTS[name]}. Style/medium: {COMMON}")
