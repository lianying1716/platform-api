# 第十二轮（2026-10-09）：放松约束，重跑。
# 上一轮的教训：COMMON 里几十条否定 + 硬编码 hex + 强制"平面纯色块/16px必认"，
# 把 image2.5 的设计能力压死了。本轮改为：讲清品牌故事 + 用途 + 气质，把发挥空间交给模型。
#
# 品牌：星采 / Stargather —— 面向中文创作者的自媒体素材采集与管理工具。
#       名字含义 = 采星（把散落的星光采集、收拢）。
# 用途：品牌主 logo，会出现在产品界面、APP 图标、favicon、登录页、名片与周边印刷。
# 气质：星空、诗意、浪漫、精致、有品质感。
#
# 品牌调色偏好（可自由发挥）：暖白 / 夜空深蓝 / 一抹砖红点缀。

BRAND = (
 "Brand: 星采 (Chinese) / Stargather (English) - a tool that helps Chinese content creators "
 "collect, organize and process media from platforms like Bilibili, Douyin and Xiaohongshu. "
 "The name 星采 literally means 'gathering stars': collecting scattered starlight and bringing it together. "
 "Audience: independent Chinese content creators, roughly 20-35 years old; they value taste, quality and "
 "a sense of craft. Desired feeling: starry, poetic, romantic, refined, calm, a little magical. "
 "This logo is the brand's primary identity mark. It will be used as the in-app brand mark, as a mobile "
 "app icon, as a browser favicon at very small sizes, on the login page, and on print and merchandise - "
 "so it needs to work both large and tiny. "
 "Suggested palette (you may use your judgment): warm off-white, deep night-sky blue, and a small brick-red "
 "accent."
)

PROMPTS = {
# 1 · 主 logo：字标 + 图形组合，交给模型定风格
"m1-primary-combination": (
 "Design the primary logo for this brand. You may choose whether to lead with the Chinese characters 星采, "
 "with a symbol, or a combination of both - decide what makes the strongest, most beautiful identity. "
 "Capture the feeling of gathering starlight: something precious being collected and brought together. "
 "I want a mark that feels well-designed and original, not like a stock icon or an emoji. "
 "Show it well-composed and centered on a suitable background, with generous space around it."
),
# 2 · 中文汉字字标
"m2-chinese-wordmark": (
 "Design an elegant Chinese wordmark (logotype) of the two characters 星采, drawn as bespoke lettering "
 "rather than a plain system font. Let the letterforms carry the starlight idea - for example the dot of "
 "星 could become a spark or a star, done tastefully. The two characters must be correct and readable. "
 "It should look like the identity of a refined creative tool."
),
# 3 · 诗意符号（图形为主）
"m3-poetic-symbol": (
 "Design a poetic, memorable symbol that expresses 'gathering starlight' - the act of collecting scattered "
 "stars and bringing them together. Find an original, elegant image rather than a generic crescent-and-star "
 "or a standard magic-sparkle icon. Think about the beauty of light being gathered, held or arranged. "
 "Keep it intentional and distinctive."
),
# 4 · 组合标记：符号 + 星采 中文字标并列
"m4-symbol-plus-wordmark": (
 "Design a combination logo for this brand: an original symbol alongside the Chinese brand name 星采 as a "
 "horizontal lockup, the way a professional identity is presented. Make the symbol and the Chinese lettering "
 "feel like one designed family. Show the whole lockup balanced and centered."
),
# 5 · 星空 / 星座灵感
"m5-celestial": (
 "Design a logo inspired by the night sky and constellations, expressing the idea of gathering stars into "
 "one place. It could draw on star charts, the Big Dipper, or the geometry of how stars connect - but the "
 "result should feel like a designed brand mark, refined and poetic, not a literal star map or an astrology "
 "clipart."
),
# 6 · 印章 / 徽标感（东方气质）
"m6-seal": (
 "Design a logo with a quiet Eastern character - think of a modern reinterpretation of a Chinese seal or a "
 "refined emblem - carrying the starlight idea for the brand 星采. It should feel contemporary and elegant "
 "rather than antique or touristy. This will be the brand's signature mark."
 ),
}
def build(name):
    return (f"Use case: logo-brand. Asset type: primary brand logo.\n"
            f"{BRAND}\n\nDirection: {PROMPTS[name]}")
