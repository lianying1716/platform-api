# 第四轮：gpt-image-2.5 + 强艺术指导。核心创意=「碎片聚合成星」：采集的本质
COMMON = (
 "flat vector logo, master-level geometric construction, solid black ink shapes on plain white, "
 "deliberate asymmetry, varied block sizes for rhythm, precise even gaps, confident chunky weights, "
 "strong memorable silhouette, generous clear margins, ONE single mark, no text, no letters, "
 "no gradient, no shading, no 3D, no mockup, no watermark"
)
PROMPTS = {
# 核心创意：多个大小不一的方块被吸向中心，拼成一个不完全对称的星
"31-shards-to-star": (
 "a four-pointed star assembled from five separate solid blocks of clearly different sizes that "
 "cluster tightly together, the blocks nearly touching with thin even gaps, the largest blocks form "
 "the vertical points and the smaller blocks form the horizontal points, slightly off-balance so it "
 "reads as pieces being drawn together into a whole rather than a perfect star"
),
# 负空间：一个方块的缺口正好是星的形状
"32-negative-star": (
 "a single heavy solid black square whose interior is carved away by a bold four-pointed star shaped "
 "cutout placed off-centre toward the lower left, leaving thick unequal borders on all four sides, "
 "the star cutout reaching and breaking through the bottom edge"
),
# 手/爪的抽象：三根等距粗指从上抓取，下面是收集到的方块
"33-grasp-blocks": (
 "three thick identical parallel fingers descending from the top edge at a slight diagonal, "
 "their tips reaching down toward two solid square blocks of different sizes resting at the bottom, "
 "the whole thing forming a compact tall rectangular composition"
),
# 字母 X + 星：把 X 做的像采集的交叉
"34-x-collect": (
 "a bold X built from two thick straight bars, where the upper right arm is noticeably thicker than "
 "the other three, and a small solid diamond is wedged into the lower left inner corner of the X, "
 "creating deliberate weight imbalance"
),
# 容器 + 溢出：方块从容器里长出来
"35-overflow-box": (
 "a thick-bottomed open rectangular container like a shallow box, with three solid squares of "
 "increasing size stacked vertically above its open mouth, the largest square floating free at top, "
 "as if items are being stacked or lifted out"
),
# 放射但不俗：星只有三个角（非常规）
"36-three-point": (
 "an unconventional star with only three uneven points, formed by three thick triangular blades "
 "radiating from a small solid hexagonal core, each blade a different length, arranged with clear "
 "rotational spacing, bold and strange like a tool or a spark"
),
}
