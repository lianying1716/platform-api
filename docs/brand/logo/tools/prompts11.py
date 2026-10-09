# 第十一轮：B/C 最后一次尝试。问题根因：任何「弧线/月牙 + 星」组合都会被读成 🌙✨ 夜间模式。
# 解法：用真正的「采集动作」意象，且带文化专属性 —— 北斗七星本质上就是一把舀星的勺子。
COMMON = (
 "professional logo design, master-level craft, elegant poetic mood, extremely minimal, "
 "few large confident shapes only, flat solid colors, absolutely no gradient, no shading, no 3D, "
 "no glow, no texture, no mockup, no watermark, no drop shadow, no moon, no crescent, no scattered dots, "
 "strong bold silhouette readable at 16 px, centered with generous even margin, single mark only, "
 "no cartoon, no emoji, no clip art, no extra text"
)
PROMPTS = {
# C-alt：北斗七星「舀星」—— 勺子就是采集器，文化专属
"c4-dipper-scoop": (
 "a minimalist poetic logo mark of the Big Dipper constellation: seven solid stars of varying sizes joined "
 "by a few short straight lines into the recognizable dipper shape, the four-star bowl at the lower left "
 "scooping up one larger bright star that sits just inside the bowl mouth; stars are warm cream "
 "(hex F3ECDD) and the single scooped star is brick red (hex CC2823); no moon, no crescent, no scattered "
 "dots; on a plain deep midnight blue background (hex 0E1A2B); bold clean shapes, elegant and refined"
),
# B-alt：非月牙的采集器 —— 一个方口广开的「兜/畚箕」接住落下的星
"b4-open-hopper": (
 "a minimalist poetic logo mark of a bold open-mouthed scoop shaped like a wide angular letter U or a "
 "shallow basket with straight sides and a flat base, tilted slightly and reaching upward to catch light; "
 "one single solid star drops straight into its open mouth; only two large shapes total; the scoop is warm "
 "cream (hex F3ECDD) and the star is solid brick red (hex CC2823); no moon, no crescent, no scattered dots, "
 "no thin lines; on a plain deep midnight blue background (hex 0E1A2B); bold simple silhouette, "
 "geometric yet graceful"
 ),
}
def build(name):
    return (f"Use case: logo-brand. Asset type: primary logo mark for a creator-tools brand named 星采 "
            f"(Stargather). Primary request: {PROMPTS[name]}. Style/medium: {COMMON}")
