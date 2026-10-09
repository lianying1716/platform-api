# 星采 Stargather · 品牌资产

**产品名**：星采 / Stargather
**定位**：自媒体创作的自动化素材管理加工
**视觉调性**：星空 / 诗意 / 浪漫 / 精致（界面本身仍走 V3 包豪斯，二者分开）

## 标志（现行）

**「月碗托星」M3**：一只开口朝上的碗/月牙托住一颗中央大星与散落星光，
右上角一颗砖红小星 —— 对应「星采 = 采星」，把散落的星光采集收拢进同一个容器。

| 项 | 值 |
|---|---|
| 夜空底 | `#0D1937` |
| 月白（碗与星） | `#F3ECDD` |
| 砖红强调 | `#CC2823` |
| 矢量母版 | `logo/m3/stargather-m3.svg` |
| 小尺寸简化版 | `logo/m3/small/stargather-m3-small.svg` |
| 资产总览 | `logo/m3/OVERVIEW.png` |
| 完整说明 | `logo/m3/README.md`（配色、资产清单、矢量化方法、复现命令） |
| 在线看 | http://127.0.0.1:9100/brand/m3-OVERVIEW.png |

## 前端接入（`web/public/`）

由 `logo-gen/build_frontend_brand.py` 从矢量母版生成，**改标志请重跑脚本，不要手改产物**：

| 文件 | 用途 |
|---|---|
| `logo-symbol.svg` | 登录页（夜空底方块，浅底上显示） |
| `logo-symbol-dark.svg` | 侧栏（**透明底**，深色侧栏上显示） |
| `logo-symbol-small.svg` | 小尺寸简化版（带底） |
| `favicon.svg` / `favicon.ico` / `favicon-16/32/48.png` | 浏览器图标（用简化版，16px 才认得出） |
| `icon-192.png` / `icon-512.png` / `maskable-512.png` | PWA / Android（完整版，大尺寸） |
| `apple-touch-icon.png` | iOS |

接入点：`web/index.html`（head 图标 + theme-color）、`web/public/site.webmanifest`、
`web/src/pages/Login.vue`（登录卡片）、`web/src/layout/Layout.vue`（侧栏顶部）。

## 历史（已被取代，仅留档）

- **「轨道 Orbit」**（四角星 + 斜环）—— 曾短暂定为正式方案，后因风格与预期不符被 M3 取代。
  见 `logo/orbit/README.md`。
- 更早的「取景框 + 星」手绘稿、以及各轮生图探索，见 `logo/concepts/`、`logo/drafts/`、
  `logo/generated/`、`logo/GENERATED.md`。**均不要再用于生产**。

## 界面配色（`web/src/styles/tokens.css` 的 V3 令牌）

界面与标志**分开**：标志用上面的夜空蓝/月白/砖红，界面继续用 V3 的墨色/米白/砖红体系。

| 用途 | OKLCH | HEX |
|---|---|---|
| 强调（砖红） | `oklch(0.55 0.20 28)` | `#CC2823` |
| 主色（墨） | `oklch(0.20 0.010 60)` | `#1A1512` |
| 底色（米白） | `oklch(0.955 0.012 88)` | `#F3F0E7` |
| 辅助（蓝） | `oklch(0.48 0.17 258)` | `#0B58BB` |
