# 第七轮：按第一轮评审反馈收敛。
# 反馈要点：A 三向楔形 = 辐射警告（废弃）；B/C 唯一致命伤是「没有星/采集语义锚点」+
# 「16px 细节粘连」。本轮：修 B，并新增两条带明确「采集/汇聚」动作且非陈词滥调的几何。
COMMON = (
 "flat vector logo mark, master-level geometric construction, solid flat shapes, "
 "exactly the colors described and nothing else, absolutely no gradient, no shading, no 3D, "
 "no outline, no stroke-only line, no texture, no mockup, no watermark, no text, no letters, "
 "crisp hard edges, very heavy bold chunky forms, wide clear negative-space gaps that survive "
 "downscaling, strong simple silhouette readable at 16 px, centered with generous even margin, "
 "single mark only, no four-point star, no five-point star, no magic wand, no robot, no gear, "
 "no radiation trefoil, no shutter blades, no targeting reticle, no pirate skull"
)
PROMPTS = {
# ---- D · Dock-refined：修同心与缝隙，注入一点「星芒」暗示 ------------------
"d1-dock-refined": (
 "an abstract geometric mark of two shapes only: a very heavy solid square slab in warm near-black "
 "ink (hex 1A1512) with a large clean circular bite removed from its upper right corner, and a single "
 "heavy solid vivid brick red (hex CC2823) disc that sits inside that circular bite, exactly concentric "
 "with the bite so a uniform visible gap of background separates the disc from the slab on the inside "
 "edge while the disc clearly protrudes beyond the square's corner on the outside, like a body sliding "
 "into a socket on a track; on a plain warm off-white background (hex F3F0E7). "
 "Two shape colors plus background, extremely blocky and readable, no rings, no outlines"
),
# ---- E · Comet-gather：彗核 + 收拢的三股尾（动作=扫拢素材） -----------------
"e1-comet-gather": (
 "an abstract geometric mark shaped like a bold comet built from exactly four solid chunky pieces: "
 "one large heavy solid vivid brick red (hex CC2823) circle at the upper right as the comet core, and "
 "three bold tapered straight bars in warm near-black ink (hex 1A1512) fanning out from the core "
 "toward the lower left in a sweeping tail, the three bars parallel and of equal thickness with wide "
 "even gaps between them, reading as a broom sweeping scattered fragments together into the core; "
 "on a plain warm off-white background (hex F3F0E7). Three flat colors total, no gradient"
),
# ---- F · Gather-into-diamond：小方块沿对角线汇入菱形 -------------------------
"f1-gather-diamond": (
 "an abstract geometric mark of five solid chunky pieces: one large heavy solid warm near-black ink "
 "(hex 1A1512) diamond (a square rotated exactly 45 degrees) at the center right, and four identical "
 "small solid vivid brick red (hex CC2823) squares arranged in a straight diagonal line marching from "
 "the lower left into the diamond, like pieces being collected and pulled into the shape, the last red "
 "square partially overlapping the diamond's edge; on a plain warm off-white background (hex F3F0E7). "
 "Two shape colors plus background, very bold and blocky, no outlines, no gradient"
),
# ---- G · Monogram-S：包豪斯硬边字母 S（无曲线，两块互锁斜板） ----------------
"g1-monogram-s": (
 "an abstract geometric monogram of the capital letter S, built from exactly two heavy solid warm "
 "near-black ink (hex 1A1512) rectangular slabs that interlock with crisp 90-degree and 45-degree cuts "
 "and no curves at all, the upper slab opening to the right and the lower slab opening to the left so "
 "the whole reads unmistakably as a bold modernist S; a single small solid vivid brick red (hex CC2823) "
 "square notch is cut into the upper right opening of the S like a collected tile snapping into place; "
 "on a plain warm off-white background (hex F3F0E7). Two shape colors plus background, ultra bold, "
 "hard-edged, geometric, no serifs"
),
}
def build(name):
    return (f"Use case: logo-brand. Asset type: logo mark for a web app favicon and sidebar. "
            f"Primary request: {PROMPTS[name]}. Style/medium: {COMMON}")
