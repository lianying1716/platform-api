# V3 包豪斯落地 + 信息架构调整（实施计划）

> 决策：用户选定 **V3 · 包豪斯** 为唯一视觉方向。本计划把它落到真实页面。
>
> **状态：已完成并验证。** 截图见 `docs/design-preview/v3/`，
> 后端 417 个测试全绿，浏览器零报错、零横向溢出。

## 一、信息架构调整

| 现状 | 调整后 | 说明 |
|---|---|---|
| 总览 `/overview` + 数据看板 `/analytics` | **合并为「总览」** `/overview` | 一个页面三段：资产总况 / 平台与分类分布 / 趋势与对标 |
| 订阅同步 `/subscriptions` + 关注博主 `/followed-creators` | **合并为「关注列表」** `/followed-creators` | 保留关注博主卡片；订阅源（收藏夹/合集/周期设置）作为同页第二个 tab |
| 关键词采集 / 素材库 / 处理队列 / 账号中心 / AI 接入 / 系统设置 | 不变 | 逐页换 V3 外壳 |

导航顺序：总览 · 账号中心 · 关键词采集 · **关注列表** · 素材库 · 处理队列 · AI 接入 · 系统设置

## 二、总览要显示的真实数据（后端 `/api/dashboard/summary` 扩展）

1. 资产总况：总素材数、**视频数 / 文章数**、总时长、今日新增、近 7 天新增
2. 平台维度：每个平台的视频数 / 文章数 / 素材数 / 播放合计 / 点赞合计
3. 分类维度：按 `auto_category` 的分布（视频理解判类结果 + 未判类）
4. 存储：已用总空间、按平台占用、存储根可用性
5. 加工：待处理 / 运行中 / 失败任务数，已转录 / 已理解数量
6. 互动：总播放 / 总点赞 / 总评论 / 总收藏（取每内容最新快照）
7. 趋势：近 14 天每日新增（抓取 vs 入库）
8. 热门 Top：近 24h / 7 天两档

## 三、素材库卡片要显示的字段

- 标题、**内容类型徽标（视频/文章/图文）**、平台徽标
- **博主**（头像 + 昵称，可点跳主页）
- **发布时间**（published_at）与**同步时间**（入库/落盘时间）
- **分类**（auto_category 中文标签 + 置信度）
- 时长（视频）/ 字数（文章）
- 播放 / 点赞 / 评论 / 收藏
- **文件体积**
- 加工状态（下载 / 转录 / 理解）
- **视频可直接播放**（HTML5 player，走 Range 流式接口）；文章可打开正文

## 四、后端改动

1. `GET /api/assets` 扩展返回字段（类型、分类、时长、体积、同步时间、可播放标记、博主头像与主页）
2. 新增 `GET /api/assets/{id}/media`：支持 HTTP Range 的视频流式播放
3. 新增 `GET /api/assets/{id}/article`：返回文章正文（供阅读视图）
4. `GET /api/dashboard/summary` 扩展为上述统计
5. `app/core/storage.py` 增加文件体积/目录体积工具

**补充（实现时新增）**

6. 登录 / 初始化同时下发 httpOnly 会话 Cookie：`<video>` 标签无法自定义请求头，
   只能靠 Cookie 鉴权；新增 `POST /api/auth/logout` 清理。
7. `scripts/seed_demo.py` 增加文章 / 图文素材与真实可解码的演示 mp4，
   让「文章筛选 + 阅读视图 + 视频播放 + 存储占用」在模拟数据上端到端可验。

## 七、V3 全站生效方式

- `main.ts` 给根元素打 `data-dir="bauhaus"`，使 `ui.css` 里所有
  `[data-dir='bauhaus']` 规则（硬阴影、0 圆角、顶部粗黑边、目录字重）全站生效。
- `tokens.css` 的 `:root` 直接落 V3 令牌，`--el-*` 桥接改为 `:root, [data-dir]` 双作用域，
  未改造的 Element 页面也自动跟随砖红 / 0 圆角 / 描边。
- `/design-preview` 样张页容器自带 `data-dir`，仍可切换三方向做对比。
- `ui.css` 新增全局 `.ui-page / .ui-tabs / .ui-tab / .ui-bar`，避免各页 scoped 重复定义。

## 五、前端改动

1. `styles/tokens.css`：把包豪斯令牌提到 `:root` 作默认，保留 `[data-dir]` 供样张页对比
2. `styles/ui.css`：沿用现有 `.ui-*` 组件层，补卡片网格、播放器、统计块
3. `layout/Layout.vue`：V3 外壳（深色侧栏 + 顶部粗黑边 + 顶部栏）
4. `pages/Overview.vue`：合并后的总览（统计块 + ECharts + 热门）
5. `pages/Assets.vue`：V3 卡片网格 + 视频播放抽屉 + 保留原有详情/转录/拆解能力
6. `pages/FollowedCreators.vue`：改名「关注列表」+ V3 卡片 + 订阅源 tab
7. `router.ts`：合并路由，`/subscriptions` 与 `/analytics` 重定向

## 六、验证

- 后端：`uv run pytest tests/ -q` 全绿
- 前端：`npm run build` 通过
- 浏览器：Playwright 逐页点按钮、确认图表渲染、视频 Range 请求返回 206、控制台零报错
- 全部走局域网 `127.0.0.1:9100`

### 实测结果（Playwright，1440×900，局域网）

| 项 | 结果 |
|---|---|
| 8 个页面页头 + 横向溢出 | 全部有 V3 页头，`scrollWidth == clientWidth == 1440` |
| 控制台报错 | 0（仅 ECharts autoresize 的 ResizeObserver 良性提示） |
| `/analytics` → `/overview` | 重定向正确，侧栏高亮跟随 |
| `/subscriptions` → `/followed-creators` | 重定向正确 |
| 视频播放 | `readyState=4`、`duration=3`、`videoWidth=320`、播放推进到 1.16s、无错误 |
| 视频 Range | `206` + `Content-Range: bytes 0-999/31255` |
| 文章筛选 + 阅读 | 2 条文章，阅读弹层渲染正文（188 字） |
| 素材详情抽屉 | 转录 / AI 分析 / 评论 / 分档加工 / 爆款拆解 全在 |
| 订阅新建弹窗 | 14 项设置项完整 |
| 关注开关落库 | UI 切换后 `/api/followed-creators` 返回一致 |
| 同步按钮 | 真实调用返回风控冷却错误并提示（演示账号无凭证） |
| 样张页三方向 | grotesk/nocturne/bauhaus 令牌与图表仍各自生效 |
