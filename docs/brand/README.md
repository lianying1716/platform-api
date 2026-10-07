# 星采 Stargather · 品牌资产

**产品名**：星采 / Stargather
**定位**：自媒体创作的自动化素材管理加工
**视觉调性**：V3 包豪斯 —— 纯几何、硬边、零圆角、零渐变、克制的砖红

## 标志

**「轨道 Orbit」**：四角星 = 被采集进来的内容；斜向椭圆环带 = 轨道。
环带在星前 / 星后交错穿插，形成「绕着星运行、把内容纳入轨道」的动势。

| 项 | 值 |
|---|---|
| 主标 | 墨星 `#1A1512` + 砖红轨 `#CC2823` / 米白底 `#F3F0E7` |
| 深底版 | 米白星 + 砖红轨 / 墨底 |
| 完整规范 | `logo/orbit/README.md`（几何参数、配色表、尺寸分级、复现命令） |
| 在线看 | http://127.0.0.1:9100/brand/orbit-gallery.html |

> 早期那套「取景框 + 星」手绘稿已被否决，仅作历史记录留在 `logo/concepts/`、`logo/drafts/`，
> **不要再用**；`logo/master/`、`logo/dist/` 是那版旧的派生文件，新接入一律走 `logo/orbit/`。

## 配色（取自 `web/src/styles/tokens.css` 的 V3 令牌）

| 用途 | OKLCH | HEX |
|---|---|---|
| 强调（砖红） | `oklch(0.55 0.20 28)` | `#CC2823` |
| 主色（墨） | `oklch(0.20 0.010 60)` | `#1A1512` |
| 底色（米白） | `oklch(0.955 0.012 88)` | `#F3F0E7` |
| 辅助（蓝） | `oklch(0.48 0.17 258)` | `#0B58BB` |

## 前端接入

`web/public/` 下的 `logo-symbol.svg`（浅底）、`logo-symbol-dark.svg`（深底 / 侧栏）、
`logo-symbol-small.svg`、`favicon.*`、`icon-*.png`、`maskable-512.png`、`apple-touch-icon.png`
均由 `logo-gen/orbit_final.py` + `orbit_assets.py` 生成，改标志时请重新跑脚本，不要手改产物。

## 平台图标

`web/src/brand/platforms.ts` 是前端平台视觉的唯一来源（图标路径 + 平台名 + 品牌色），
由 `PlatformMark.vue` 渲染。几何来自 **simple-icons（CC0-1.0）**；
抖音用 simple-icons 的 `tiktok` 路径（两者共用同一音符商标几何）。

**合规边界**：平台商标归各平台所有，仅作「这是哪个平台」的识别性使用，
不得作为本项目自身标志、不得暗示官方合作或背书。详见
`../../docs/license-and-usage-compliance.md` 第 5 节（工作区根目录）。
