# 星采 Stargather · M3「月碗托星」标志（正式选型）

用户于 2026-10-09 从第 5 轮候选里**选定 M3** 作为品牌标志。

- **源概念图（权威母本）**：`../generated/raw12/m3-poetic-symbol.png`
  （`logo-gen/raw12/m3-poetic-symbol.png` 为 1254px 原始文件）
- **图形语义**：一只开口朝上的碗/月牙，托住一颗中央大星与散落的星光，右上角一颗砖红小星。
  对应「星采 = 采星」——把散落的星光采集、收拢进同一个容器。
- **气质**：星空 / 诗意 / 浪漫 / 精致。

## 配色（取自该图，已对齐产品 V3 令牌观感）

| 用途 | HEX |
|---|---|
| 夜空底 | `#0D1937` |
| 月白 / 碗与星 | `#F3ECDD` |
| 砖红强调 | `#CC2823` |
| 碗的暖金受光 | `~#E2B678` |

## 资产（`dist/`）

| 文件 | 用途 |
|---|---|
| `full-lockup.png` | 完整版（符号 + 星采 + Stargather），登录页/印刷 |
| `symbol-master-1254.png` | 符号 1254px 母本 |
| `symbol-1024/512/.../16.png` | 符号尺寸阶梯 |
| `favicon.ico` | 浏览器图标（16/32/48/64） |
| `apple-touch-icon.png` / `maskable-512.png` | iOS / PWA（含安全边距） |

网页副本在 `platform-api/web/public/brand/m3/`。

## 重要说明（必读）

1. **矢量母版已产出**：`stargather-m3.svg`（由 `logo-gen/vectorize.py` 管线生成，~16KB，纯路径无位图）。
   做法见下方「矢量化方法」。
2. **小尺寸另有一套简化版**：`small/`（去掉细星与交叉弧，只留碗+中央星+红星），
   专供 favicon / 侧栏 24px 以下使用——完整版在 16px 会糊。

## 矢量化方法（本仓库已验证）

原概念图带渐变与颗粒质感，**直接描摹必崩**。可用管线 = 两段式：

```
位图概念图 --(gpt-image-2.5 扁平化)--> 纯色平涂图 --(potrace 分层描摹)--> 干净 SVG
```

实测对比（同一张 M3）：

| 方法 | 结果 |
|---|---|
| vtracer 彩色描摹 | 边缘毛刺、丢光晕 · 2–5 分 |
| potrace 直接描原图 | 渐变被切成碎色块 · 6 分 |
| 视觉大模型直接生成 SVG | 结构走形、悬空线 · 4 分 |
| **扁平化 + potrace（本方案）** | 干净平滑无噪点 · **9 分** |

命令（首次跑会调一次 gpt-image-2.5 做扁平化；之后可用 `--flat` 复用、不再联网）：

```bash
# 完整流程
python3 logo-gen/vectorize.py logo-gen/m3-pipeline/m3-symbol-src.png out.svg \
    --palette "13,25,55;243,236,221;204,40,35" --viewbox 256

# 复用已扁平化的中间图（离线、确定性，本仓库 M3 用的就是这条）
python3 logo-gen/vectorize.py logo-gen/m3-pipeline/m3-symbol-src.png out.svg \
    --palette "13,25,55;243,236,221;204,40,35" \
    --flat logo-gen/m3-pipeline/m3-flat.png --viewbox 256
```

中间产物 `logo-gen/m3-pipeline/` 已随仓库保留（`m3-flat.png` = 扁平化结果，`m3-simple-flat.png` = 小尺寸简化版源）。

依赖：`potracer`（pip，纯 Python 实现）、`cairosvg`、`Pillow`、`scipy`、`numpy`。

## 复现

```bash
python3 logo-gen/m3_assets.py       # 从矢量母版重新导出 dist/ 与网页副本
```
