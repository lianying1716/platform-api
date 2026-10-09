# 第九轮（2026-10-09）：换方向 —— 按用户反馈「星采 = 采星，要星空/诗意/浪漫」。
# 不再走包豪斯几何；logo 与 UI 解耦。三条方向各出一款：
#   A 汉字字标「星采」  —— 专属字形，把星意织进笔画
#   B 意象符号（采星）  —— 有画面：手捧星 / 兜收星
#   C 星空诗意符号      —— 抽象：星群聚拢 / 星轨拖尾
#
# 调性：星空、诗意、浪漫、精致、克制；平面无渐变；深色夜空底 + 暖白星光 + 一点品牌砖红。
COMMON = (
 "professional logo design, master-level craft, elegant and poetic mood, inspired by a starry night sky, "
 "refined minimal shapes with graceful curves, flat solid colors, absolutely no gradient, no shading, "
 "no 3D, no glow, no texture, no mockup, no watermark, no drop shadow, "
 "strong clean silhouette that still reads at small sizes, centered with generous even margin, "
 "single mark only, no cartoon, no emoji style, no clip art, no extra decorative text"
)
PROMPTS = {
# ---------- A · 汉字字标「星采」 ----------
"a1-wordmark-serif": (
 "a Chinese wordmark logo of exactly two characters 星采 (xīng cǎi) set side by side, rendered as refined "
 "custom serif lettering with elegant high-contrast strokes; the two characters are correct and verbatim, "
 "cleanly drawn, with one subtle star sparkle replacing the small dot inside the upper part of 星; "
 "the lettering is warm cream (hex F3ECDD) on a plain deep midnight blue background (hex 0E1A2B); "
 "one tiny brick red (hex CC2823) star mark sits as a period-sized accent at the lower right of the word"
),
"a2-wordmark-modern": (
 "a Chinese wordmark logo of exactly two characters 星采 (xīng cǎi) set side by side, rendered as modern "
 "geometric custom lettering with crisp even strokes and a slight poetic lift, the two characters correct "
 "and verbatim; the strokes of 采 are drawn so they gently converge like light gathering inward; "
 "warm cream (hex F3ECDD) lettering on a plain deep midnight blue background (hex 0E1A2B), "
 "with a single small brick red (hex CC2823) four-point sparkle tucked above the first character"
),
# ---------- B · 意象符号（采星） ----------
"b1-hand-cup-star": (
 "a minimalist poetic logo mark of a slender elegant hand, seen from the side, gently cupping and lifting "
 "one small glowing star; the hand is drawn with a few confident smooth lines in warm cream (hex F3ECDD), "
 "the star is a simple solid five-point star in soft warm gold-cream, and a sparse scatter of tiny dots "
 "trails behind the star like stardust; a single small brick red (hex CC2823) accent star sits in the trail; "
 "on a plain deep midnight blue background (hex 0E1A2B). Flat solid shapes, no outline-only line art"
),
"b2-pouch-gather": (
 "a minimalist poetic logo mark of a small elegant open crescent-shaped vessel or scoop reaching up, "
 "catching three tiny stars that fall into it like gathered light; the vessel is a bold smooth crescent "
 "shape in warm cream (hex F3ECDD), the falling stars are solid small shapes, one of them brick red "
 "(hex CC2823) and two warm cream, with a few tiny stardust dots; on a plain deep midnight blue "
 "background (hex 0E1A2B). Flat solid shapes, refined and romantic, no outline-only line art"
),
# ---------- C · 星空诗意符号 ----------
"c1-gather-constellation": (
 "an abstract poetic logo mark: a small elegant asterism of five solid stars of different sizes connected "
 "by thin graceful arcs that spiral inward and gather together into one larger bright star at the center; "
 "stars are warm cream (hex F3ECDD) with the central star in soft warm gold, and one small star brick red "
 "(hex CC2823); faint tiny dots suggest deeper sky; on a plain deep midnight blue background (hex 0E1A2B). "
 "Flat solid shapes, delicate and balanced, no outline-only line art, no gradient"
),
"c2-star-trail": (
 "an abstract poetic logo mark of one bright star dragging a long graceful curved trail that sweeps around "
 "into a gentle spiral, as if sweeping scattered stardust together; the star head is a clean solid shape "
 "in warm cream (hex F3ECDD) and the sweeping trail is a bold smooth band in brick red (hex CC2823) that "
 "tapers elegantly to a point, with a few tiny cream dots along it; on a plain deep midnight blue "
 "background (hex 0E1A2B). Flat solid shapes, refined and romantic, no outline-only line art"
),
}
def build(name):
    return (f"Use case: logo-brand. Asset type: primary logo mark for a creator-tools brand named 星采 "
            f"(Stargather). Primary request: {PROMPTS[name]}. Style/medium: {COMMON}")
