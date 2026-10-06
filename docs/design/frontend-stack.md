# 前端组件与图表技术选型（调研结论）

> 日期：2026-10-06 · 目标：给「素材采集工作台」定一套**够全、够现代、可深度定制**的组件与图表底座。
> 现状：Vue 3 + Vite + Element Plus 2.9，只有表格/表单/弹窗，**没有任何图表**。
>
> 状态：**已落地并验证**。图表、令牌、自建组件层均已在 `feat/ui-preview` 分支实现，
> 见 `docs/design-preview/`（27 张截图 + 在线可交互样张 `/design-preview`）。

---

## 0. 已实现（本轮）

| 文件 | 作用 |
|---|---|
| `web/src/styles/tokens.css` | 三套设计令牌（`[data-dir]` 作用域）+ `--el-*` 桥接 |
| `web/src/styles/ui.css` | 自建组件层：按钮/分段/徽标/开关/面板/表格/骨架/toast |
| `web/src/components/charts/EChart.vue` | ECharts 统一封装，按需注册，颜色读设计令牌 |
| `web/src/pages/DesignPreview.vue` | 9 页 × 3 方向的可交互样张 |

图表种类：折线(含 dataZoom)、柱状、环形、散点四象限(markLine)、迷你走势、**brush 框选联动**、
热力图、漏斗、雷达、日历热力、桑基(可拖拽)、仪表盘。
---

## 1. 结论（本轮采用）

| 层 | 选型 | 版本 | 理由 |
|---|---|---|---|
| 图表引擎 | **Apache ECharts** | 6.1 | 交互最全（tooltip / 缩放 / 图例开关 / brush 框选 / 联动），中文生态最好，按需引入可控体积 |
| Vue 图表封装 | **vue-echarts** | 8.3 | 官方推荐封装，响应式、SSR 友好、支持 `manual-update` 高性能模式 |
| 组合式工具 | **@vueuse/core** | 13.9 | 手势/观察器/状态（暗色切换、resize、localStorage），少写样板 |
| 组件底座 | **Element Plus**（保留） | 2.9 | 表格/表单/弹窗/虚拟滚动等「行为复杂件」现成且已接入；不上第二套组件库 |
| 视觉层 | **自建设计令牌 + 定制组件** | — | 用 CSS 变量 + `--el-*` 桥接彻底改掉 Element 默认观感，避免「一眼 Element」 |

**为什么不上 shadcn-vue / Naive UI / PrimeVue：**

- **shadcn-vue（Tailwind v4 + Reka UI）**：设计自由度最高、最现代，但要**引入 Tailwind 构建链**并**重写全部 10 个页面**，迁移成本与回归风险最大，且它的图表走 Unovis（生态弱于 ECharts）。→ 作为「二期候选」。
- **Naive UI**：Vue 3 原生、主题配置强，但换库等于重写全部页面，收益不抵成本。
- **PrimeVue**：组件最多（90+），但视觉是另一套「企业主题」，同样要全量迁移。

> 关键判断：本项目**瓶颈不是组件数量，而是视觉与图表缺失**。所以保留 Element 的行为能力，**重做视觉层 + 补 ECharts**，性价比最高。

## 2. 本项目需要的组件清单（按页面）

### 数据展示
| 组件 | 用在哪 | 状态 |
|---|---|---|
| 图表：折线/面积（趋势） | 数据看板、总览 | 本轮新增 |
| 图表：柱状/堆叠 | 平台对比、任务分布 | 本轮新增 |
| 图表：环形/玫瑰 | 平台占比 | 本轮新增 |
| 图表：散点/气泡 | 爆款四象限（播放 × 互动率） | 本轮新增 |
| 图表：迷你走势（sparkline） | 指标卡内嵌 | 本轮新增 |
| 高级表格（排序/筛选/固定列/虚拟滚动） | 素材库、账号、订阅 | Element 已有 |
| 数据卡片（带上下文与环比） | 总览 | 本轮重做 |
| 时间线 | 处理队列、任务日志 | Element 已有 |

### 交互
| 组件 | 用在哪 | 状态 |
|---|---|---|
| 分段控件（Segmented） | 录入方式、时间范围 | 本轮新增 |
| 抽屉（Drawer） | 素材详情、同步历史 | Element 已有 |
| 命令面板（Command Palette, ⌘K） | 全局跳转/操作 | 本轮新增 |
| 图表框选联动（brush） | 看板筛选时间窗 | 本轮新增 |
| 骨架屏 / 空态 / 错误态 | 全站 | 本轮统一 |

## 3. 图表交互能力（选定 ECharts 的核心理由）

- **tooltip**：hover 看具体数值（带坐标轴十字准星）
- **dataZoom**：拖拽缩放时间窗（趋势图必备）
- **legend**：点击图例切换系列显隐
- **brush**：框选区间 → 联动过滤其它图表
- **联动（connect）**：多图同一 tooltip 同步
- **导出**：`getDataURL` 一键出 PNG（报告场景）

## 4. 体积控制

vue-echarts 支持按需注册：

```ts
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, PieChart, ScatterChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent } from 'echarts/components'
use([CanvasRenderer, LineChart, BarChart, PieChart, ScatterChart,
     GridComponent, TooltipComponent, LegendComponent, DataZoomComponent])
```

## 5. 落地顺序（方向敲定后）

1. 令牌层：`styles/tokens.css`（颜色/字号/间距/圆角/阴影/图表主题）
2. 图表层：`components/charts/*`（LineTrend / BarCompare / DonutShare / ScatterQuadrant）
3. 组件层：`components/ui/*`（MetricCard / Segmented / Badge / EmptyState / Skeleton）
4. 布局层：侧栏/顶栏/命令面板
5. 页面层：按 总览 → 账号中心 → 关注博主 → 订阅同步 → 素材库 → 处理队列 → 看板 → 设置 逐页替换
