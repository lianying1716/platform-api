# 星采 Stargather · 品牌资产

**产品名**：星采 / Stargather
**定位**：自媒体创作的自动化素材管理加工
**视觉调性**：V3 包豪斯 —— 纯几何、硬边、零圆角、零渐变、克制的砖红

## 标志

取景框 + 中心四角星：取景框 = 采集与聚焦，星 = 星采，也是「星算」家族的低调呼应。

| 文件 | 用途 |
|---|---|
| `logo/master/stargather-symbol.svg` | 主母版（砖红框 + 墨色星，浅底用） |
| `logo/master/stargather-symbol-ink.svg` | 单色母版（全墨色，用于派生黑白稿） |
| `logo/master/stargather-symbol-small.svg` | 小尺寸切削版（框加厚、星放大，16px 用） |
| `logo/dist/stargather-symbol-favicon.svg` | favicon 专用（紧边） |
| `logo/dist/stargather-symbol-app-icon.svg` | 圆角方块 app icon |
| `logo/dist/*-black/-white/-mono-*.svg` | 单色变体 |
| `logo/dist/*.png` | 32 / 128 / 512 / 1024 px 位图 |
| `logo/dist/favicon.ico` | 16/32/48 多尺寸 ico |

深色底版本：`web/public/logo-symbol-dark.svg`（wire 版，星改浅色）。

## 品牌色（与 `web/src/styles/tokens.css` 一致）

| 用途 | OKLCH | HEX |
|---|---|---|
| 强调 · 砖红 | `oklch(0.55 0.20 28)` | `#CC2823` |
| 主色 · 墨 | `oklch(0.20 0.010 60)` | `#1A1512` |
| 底色 · 米白 | `oklch(0.955 0.012 88)` | `#F3F0E7` |
| 辅助 · 蓝 | `oklch(0.48 0.17 258)` | `#0B58BB` |

## 使用规范

- **最小尺寸**：主母版 ≥ 24px；≤ 24px 请用 `-small` 或 `-favicon` 版本
- **留白**：四周 ≥ 标志高度的 1/4
- **允许**：单色、反白（深底用 `-white` 或 `-dark`）、居中缩放
- **禁止**：拉伸变形、加渐变/阴影、改配色、在花哨底图上直接叠放

## 已接入项目

| 位置 | 内容 |
|---|---|
| `web/index.html` | title、favicon.ico / favicon.svg / apple-touch-icon / manifest、theme-color |
| `web/public/` | 全套图标 + `logo-symbol.svg` + `logo-symbol-dark.svg` |
| `web/src/layout/Layout.vue` | 侧栏标志（深色底版）+「星采 · Stargather」 |
| `web/src/pages/Login.vue` | 登录页标志、产品名、定位语 |

## 复现

```bash
# 几何审计（应全部 100/100）
python3 ~/.codex/skills/logo-design/scripts/svg_audit.py logo/master/*.svg
# 重新导出全套变体与 web 图标
python3 ~/.codex/skills/logo-design/scripts/export_variants.py \
  logo/master/stargather-symbol.svg --out-dir logo/dist \
  --name stargather-symbol --title "星采 Stargather" \
  --mono "#CC2823" --icon-bg "#CC2823" --icon-fg "#FFFFFF" \
  --favicon-source logo/master/stargather-symbol-small.svg \
  --web-icons --png 32 128 512 1024
```
