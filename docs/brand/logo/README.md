# 星采 Stargather · Logo 概念（第一轮）

产品名：**星采 / Stargather**
定位：自媒体创作的自动化素材管理加工
视觉调性：延续「V3 包豪斯」但不加重 —— 纯几何、单色、硬边、零渐变

## 在线看（局域网）

```
http://127.0.0.1:9100/brand/logo-preview.html
```

一页看完 6 个方案，含 64 / 32 / 16 px 缩放测试与单色反白测试。

## 六个方向

| 文件 | 名称 | 一句话 |
|---|---|---|
| `concepts/a-blade.svg` | A · 聚合刀锋 | 四片刀锋向中心汇聚成星，采集的动作感最强 |
| `concepts/b-star.svg` | B · 实心四角星 | 最简最满幅，纯几何，16px 依然认得出 |
| `concepts/c-finder.svg` | C · 取景框 + 星 | 取景框锁定中心星：采集 + 聚焦，工具属性明确 |
| `concepts/d-inward.svg` | D · 内向箭头 | 四枚箭头指向中心小星，语义最直白 |
| `concepts/e-star-hole.svg` | E · 星 + 方孔 | 星形中央留方孔，最轻、留白最多 |
| `concepts/f-finder2.svg` | F · 轻取景框 | 取景框更细、星更大，比 C 轻盈 |

- `*-accent.svg`：同样的图形换成品牌砖红
- `renders/`：512 / 128 / 32 px 的 PNG
- `concepts/round1-sheet.png`：概念总览板（灰度）
- `drafts/`：探索过程稿，已淘汰，仅留档

## 品牌色（取自 tokens.css 的 V3 令牌）

| 用途 | OKLCH | HEX |
|---|---|---|
| 强调（砖红） | `oklch(0.55 0.20 28)` | `#CC2823` |
| 主色（墨） | `oklch(0.20 0.010 60)` | `#1A1512` |
| 底色（米白） | `oklch(0.955 0.012 88)` | `#F3F0E7` |
| 辅助（蓝） | `oklch(0.48 0.17 258)` | `#0B58BB` |

## 几何规范

- 画布 `viewBox="0 0 256 256"`，留白约 4–8%
- 边角统一锁定 **15°**（避免 0.3–3° 的「近失误角」，审计脚本会报错）
- 四角星内顶点固定在 45° 对角线上，内半径由公式反解，保证每条边角度精确
- 全部单色填充、无渐变、无滤镜、无 `<text>`、无嵌入位图
- `svg_audit.py` 评分：6 个方案全部 **100/100**

## 复核工具

```bash
# 几何审计
python3 ~/.codex/skills/logo-design/scripts/svg_audit.py concepts/*.svg
# 概念总览板
python3 ~/.codex/skills/logo-design/scripts/concept_sheet.py concepts/a-blade.svg ... -o sheet.png
```

## 下一步

选定方向后生成完整套件：配色规范、横版/竖版组合、小尺寸切削版、favicon +
app icon + web icon 全套、行业化演示板（README / 终端 / 卡片）、一页使用规范。

## 复现脚本（tools/）

| 脚本 | 用途 |
|---|---|
| `gen-concepts.py` | 生成 A/B/C 三个概念（含精确边角公式） |
| `gen-concepts-extra.py` | 生成 D/E/F 三个概念 |
| `render-variants.py` | 换成品牌色并渲染 512/128/32 px PNG |
| `build-preview.py` | 生成可在线查看的对比页 HTML |
| `ascii-preview.py` | 把 SVG 渲染成 ASCII 校形（无图像能力时的替代眼睛） |

> 说明：本次设计在**无法查看图像**的环境下完成，形状正确性靠
> ①代码精确计算几何 ②`svg_audit.py` 程序化审计 ③渲染成 ASCII 复核
> 三重手段替代肉眼。挑选时请以浏览器实际渲染为准。
