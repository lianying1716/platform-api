# 星采 Stargather ·「轨道 Orbit」标志交付

用户从 24 款生图候选里选中 **E 轨道**，反馈「这个还不错，但只有黑色不行」。
本目录是它的**矢量重绘 + 完整配色系统 + 全套生产资产**。

在线看（局域网）：

    http://127.0.0.1:9100/brand/orbit-gallery.html

## 图形语义

四角星 = 被采集进来的内容 / 卫星；斜向椭圆环带 = 轨道。
环带在星**前**（右上段）与星**后**（左下段）交错，形成「绕着星运行、把内容纳入轨道」的动势，
对应产品「自动化素材管理加工」——不是静止的图标，而是有运动方向的动作。

## 几何规范（评审选优，代号 g2）

| 参数 | 主标 g2 | 小尺寸 s4 | 侧栏 SIDEBAR |
|---|---|---|---|
| 星尖半径 | 80 | 92 | 98 |
| 内半径比 | 0.5176 | 0.5176 | 0.54 |
| 轨道半长轴 × 半短轴 | 82 × 30 | 70 × 30 | 62 × 28 |
| 轨道带宽 | 15 | 20 | 24 |
| 轨道倾角 | −22° | −25° | −28° |
| 穿插缝宽 | 4 | 4 | 5 |

- 内半径比锁定 **0.5176**（= √2/(1+√3)），使星臂边恰好落在 **60°** 干净角上，避免「近失误角」。
- 画布 `viewBox="0 0 256 256"`，图形 bbox 48–208，四周留白 18.75%。
- 全部为**真实路径**（shapely 布尔运算烘焙），**不含 mask / 位图 / 渐变 / 文本**，
  可直接用于切割、刺绣、单色化。锚点约 113（简化后），10KB → 2KB。

## 配色（双色系统，不再是纯黑）

| 用途 | 星 | 轨道 | 底色 |
|---|---|---|---|
| **主标** `primary` | 墨 `#1A1512` | 砖红 `#CC2823` | 米白 `#F3F0E7` |
| **深底版** `inverse` | 米白 `#F3F0E7` | 砖红 `#CC2823` | 墨 `#1A1512` |
| 辅助 `blue` | 墨 | 蓝 `#0B58BB` | 米白 |
| 单色 `mono-ink` / `mono-cream` | 墨 / 米白 | 同星 | 米白 / 墨 |

配色全部取自 `web/src/styles/tokens.css` 的 V3 包豪斯令牌，与产品界面同一套颜色。

> 单色版必须用**更宽的缝（8）**：同色时只能靠底色缝表达前后层次，
> 缝太窄缩到 16px 就会糊成一坨（已由视觉模型实测确认 gap8 为甜点值）。

## 尺寸分级（关键）

| 尺寸 | 用哪个 | 说明 |
|---|---|---|
| ≤ 24px | `small` / 侧栏几何 | 星放大、轨道加粗、缝加宽，16px 仍能分清两层 |
| ≥ 32px | `primary` | 标准几何 |

实测：标准几何 16px 会退化成「红圈套黑点」；小尺寸版 16px 仍可辨认。所以
favicon 与侧栏**必须**用小尺寸几何，不能直接缩主标。

## 文件

```
master/  stargather-orbit-{primary,inverse,blue,small,mono-ink,mono-cream}.svg   母版
master/  stargather-orbit-master.svg   单色大师版（派生 favicon / 单色用）
dist/    favicon.ico / favicon.svg / apple-touch-icon.png / icon-192 / icon-512 / maskable-512
dist/    app-icon-{512,light-512,dark-512}.png
```

前端接入（`web/public/`）：`logo-symbol.svg`（浅底）、`logo-symbol-dark.svg`（深底 / 侧栏）、
`logo-symbol-small.svg`、`favicon.*`、`icon-*.png`、`maskable-512.png`、`apple-touch-icon.png`。

## 复现

```bash
python3 logo-gen/orbit_final.py     # 生成母版 SVG
python3 logo-gen/orbit_assets.py    # 生成网页图标 / APP 图标
python3 logo-gen/orbit_gallery.py   # 生成交付画廊 + 横版组合
python3 ~/.codex/skills/logo-design/scripts/svg_audit.py docs/brand/logo/orbit/*.svg --bg "#F3F0E7"
```

审计：主标 91–92/100（扣分为「环上采样折线的近失误角」，属曲线固有，非失误）。
