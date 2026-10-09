# 第八轮：精修两个 7 分方向 + 一条新概念，统一解决「语义锚点 + 16px 粘连」。
COMMON = (
 "flat vector logo mark, master-level geometric construction, solid flat shapes, "
 "exactly the colors described and nothing else, absolutely no gradient, no shading, no 3D, "
 "no outline, no stroke-only line, no texture, no mockup, no watermark, no text, no letters, "
 "crisp hard edges, very heavy bold chunky forms, wide clear negative-space gaps that survive "
 "downscaling, strong simple silhouette readable at 16 px, centered with generous even margin, "
 "single mark only, no four-point star, no five-point star, no magic wand, no robot, no gear, "
 "no radiation trefoil, no shutter blades, no targeting reticle, no spiral, no pixel trail"
)
PROMPTS = {
# H · dock v2：红圆放大、缝隙等距、红圆内切一个方孔 = 被收进来的「素材块」
"h1-dock-node": (
 "an abstract geometric mark: a very heavy solid square slab in warm near-black ink (hex 1A1512) whose "
 "upper right corner has a large clean quarter-circle bite, and one large heavy solid vivid brick red "
 "(hex CC2823) disc seated concentrically in that bite, leaving a uniform even arc of background "
 "between the disc and the slab so they never touch, with the disc clearly bulging beyond the square "
 "corner; a small clean square hole is cut out of the very center of the red disc, so the disc reads as "
 "a collected block locked into a socket; on a plain warm off-white background (hex F3F0E7). "
 "Two shape colors plus background, extremely blocky, no rings, no outlines"
),
# I · monogram S v2：统一 45° 几何，红方块明确「嵌入」S 的开口
"i1-monogram-s-tile": (
 "an abstract geometric monogram of a heavy bold capital letter S, built ONLY from straight hard-edged "
 "blocks and 45-degree diagonal cuts with no curves whatsoever, in warm near-black ink (hex 1A1512); "
 "the S has a wide rectangular open mouth at its upper right, and one solid vivid brick red (hex CC2823) "
 "square tile sits exactly inside that mouth, touching the black on three sides with one equal-width "
 "background gap all around, so it reads as a tile being slotted into the letter; "
 "on a plain warm off-white background (hex F3F0E7). Two shape colors plus background, ultra bold, "
 "strictly 45-degree geometry, no serifs, no thin strokes"
),
# J · tray/collect：宽托盘 + 三块自上而下落入 = 采集入库
"j1-collect-tray": (
 "an abstract geometric mark of two parts: a very heavy solid warm near-black ink (hex 1A1512) open "
 "rectangular tray or shallow bracket with thick walls and a wide flat base, opening upward; and exactly "
 "three solid vivid brick red (hex CC2823) squares of equal size stacked in a neat vertical column directly "
 "above the tray mouth, the lowest square just entering the tray, the other two above it with equal gaps; "
 "on a plain warm off-white background (hex F3F0E7). Two shape colors plus background, extremely blocky "
 "and balanced, hard edges, no outlines, no gradient"
),
}
def build(name):
    return (f"Use case: logo-brand. Asset type: logo mark for a web app favicon and sidebar. "
            f"Primary request: {PROMPTS[name]}. Style/medium: {COMMON}")
