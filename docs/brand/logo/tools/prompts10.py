# 第十轮：修 B / C 两个方向（评审指出「emoji 感」+「碎点小尺寸糊」），并给 A 补一版小尺寸切削字重。
COMMON = (
 "professional logo design, master-level craft, elegant poetic mood inspired by a starry night, "
 "extremely minimal, few large confident shapes, flat solid colors, absolutely no gradient, no shading, "
 "no 3D, no glow, no texture, no mockup, no watermark, no drop shadow, no scattered tiny dots, "
 "strong bold silhouette that reads at 16 px, centered with generous even margin, single mark only, "
 "no cartoon, no emoji, no clip art, no extra text, no compass rose, no many-pointed star"
)
PROMPTS = {
# B-修：把手抽象成一道托举的弧，去掉碎点，只手 + 一颗星
"b3-lifting-arc": (
 "an abstract poetic logo mark of a bold, smooth, upward-curving crescent-like arc that reaches up like an "
 "open hand or cradle cupping light; a single solid star rests in the hollow of the arc; only two large "
 "shapes total, no small dots at all; the arc is warm cream (hex F3ECDD) and the single star above it is "
 "solid soft warm gold, on a plain deep midnight blue background (hex 0E1A2B); "
 "very bold simple silhouette, no thin lines, no outline-only line art"
),
# C-修：极简「星光被弧线拢成一点」，不放碎星
"c3-arc-gather-star": (
 "an abstract poetic logo mark of one bold open circular arc that sweeps from the left and tapers to a "
 "sharp point, spiraling inward to gather a cluster of starlight that forms one single bright star at the "
 "tip; only two elements: the sweeping arc in deep brick red (hex CC2823) and one solid warm cream "
 "(hex F3ECDD) star at the center it curls around; no scattered dots, no compass, no multiple stars; "
 "on a plain deep midnight blue background (hex 0E1A2B); bold simple silhouette, elegant, no thin lines"
),
# A-补：小尺寸切削字重（更粗更简，笔画少细节）
"a3-wordmark-bold-cut": (
 "a Chinese wordmark logo of exactly two characters 星采 (xīng cǎi), drawn as very bold heavy custom "
 "lettering with thick even strokes and simplified geometric structure so it stays crisp when tiny; "
 "the two characters must be correct and verbatim; the small dot stroke at the top of 星 is a simple "
 "solid four-point sparkle; one small brick red (hex CC2823) sparkle sits between the two characters; "
 "lettering in warm cream (hex F3ECDD) on a plain deep midnight blue background (hex 0E1A2B), "
 "no thin strokes, no fine serifs, strong and clean"
 ),
}
def build(name):
    return (f"Use case: logo-brand. Asset type: primary logo mark for a creator-tools brand named 星采 "
            f"(Stargather). Primary request: {PROMPTS[name]}. Style/medium: {COMMON}")
