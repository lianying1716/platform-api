<template>
  <div :data-dir="dir">
    <!-- 仅用于敲定方向的切换条（正式版会移除） -->
    <div class="ui-switchbar">
      <span class="t">界面样张 v2</span>
      <span class="s">可交互 · 真实图表 · 点按钮都有反应</span>
      <div class="pgroup" style="display: flex; gap: 6px">
        <button v-for="p in pages" :key="p.key" :class="{ on: page === p.key }" @click="page = p.key">
          {{ p.label }}
        </button>
      </div>
      <div class="grow" />
      <span class="s">视觉方向</span>
      <button v-for="d in dirs" :key="d.key" :class="{ on: dir === d.key }" @click="dir = d.key">
        {{ d.label }}
      </button>
    </div>

    <div class="ui-app">
      <!-- 侧栏（可点，切页） -->
      <aside class="ui-side">
        <div class="ui-brand"><span class="mark">素</span>素材采集工作台</div>
        <nav class="ui-nav">
          <button
            v-for="n in nav"
            :key="n.t"
            class="ui-nav-item"
            :class="{ on: n.key === page }"
            @click="n.key && (page = n.key)"
          >
            <span>{{ n.t }}</span>
            <span v-if="n.k" class="k">{{ n.k }}</span>
          </button>
        </nav>
        <div class="ui-side-foot">
          <div class="ui-user"><span class="ui-dot ok" />admin · 本机</div>
        </div>
      </aside>

      <div class="ui-main">
        <header class="ui-top">
          <span class="ui-crumb"><b>{{ pageTitle }}</b></span>
          <div class="grow" />
          <div class="ui-search" @click="toast('命令面板：⌘K 全局搜索/跳转（示例）')">
            <span>搜索账号 / 素材 / 任务</span><kbd>⌘K</kbd>
          </div>
          <button class="ui-btn sm" @click="toast('已刷新')">刷新</button>
        </header>

        <div class="ui-content">
          <!-- ============ 总览 ============ -->
          <template v-if="page === 'overview'">
            <div class="ui-head">
              <h1>总览</h1>
              <span class="sub">近 24 小时 · 抖音 / B站</span>
              <div class="grow" />
              <div class="ui-seg">
                <button v-for="r in ranges" :key="r" :class="{ on: range === r }" @click="range = r">
                  {{ r }}
                </button>
              </div>
            </div>

            <!-- 指标：不用「大数字英雄模板」，每个都带环比与迷你走势 -->
            <div class="ui-metrics">
              <div class="ui-metric">
                <div class="k">今日抓取</div>
                <div class="v">{{ m.crawled }}</div>
                <div class="d"><span class="ui-badge ok">+12%</span> 较昨日</div>
                <div class="spark"><EChart :build="sparkUp" /></div>
              </div>
              <div class="ui-metric">
                <div class="k">入库素材</div>
                <div class="v">{{ m.assets }}</div>
                <div class="d"><span class="ui-badge ok">+8%</span> 较昨日</div>
                <div class="spark"><EChart :build="sparkUp2" /></div>
              </div>
              <div class="ui-metric">
                <div class="k">待处理任务</div>
                <div class="v">{{ m.pending }}</div>
                <div class="d"><span class="ui-badge warn">需关注</span></div>
                <div class="spark"><EChart :build="sparkFlat" /></div>
              </div>
              <div class="ui-metric alert">
                <div class="k"><span class="ui-dot bad" />失败任务</div>
                <div class="v">{{ m.failed }}</div>
                <div class="d"><button class="ui-btn ghost sm" @click="page = 'pipeline'">去处理</button></div>
                <div class="spark"><EChart :build="sparkDown" /></div>
              </div>
            </div>

            <!-- 趋势（真实交互：缩放 + 图例切换 + tooltip 十字准星） -->
            <section class="ui-panel">
              <div class="ui-panel-head">
                <h2>抓取与入库趋势</h2>
                <span class="hint">拖拽下方滑块缩放 · 点图例切换系列</span>
                <div class="grow" />
                <div class="ui-seg">
                  <button :class="{ on: metricMode === 'count' }" @click="metricMode = 'count'">条数</button>
                  <button :class="{ on: metricMode === 'play' }" @click="metricMode = 'play'">播放量</button>
                </div>
              </div>
              <div class="ui-panel-body ui-chart tall"><EChart :build="trendChart" /></div>
            </section>

            <!-- 两栏：平台占比（环形）+ 爆款四象限（散点） -->
            <div class="ui-cols">
              <section class="ui-panel">
                <div class="ui-panel-head"><h2>平台占比</h2><span class="hint">按入库量</span></div>
                <div class="ui-panel-body ui-chart"><EChart :build="shareChart" /></div>
              </section>
              <section class="ui-panel">
                <div class="ui-panel-head">
                  <h2>爆款四象限</h2>
                  <span class="hint">播放 × 互动率 · 右上为爆款</span>
                </div>
                <div class="ui-panel-body ui-chart"><EChart :build="quadrantChart" /></div>
              </section>
            </div>
          </template>

          <!-- ============ 数据看板 ============ -->
          <template v-else-if="page === 'analytics'">
            <div class="ui-head">
              <h1>数据看板</h1>
              <span class="sub">对标账号与爆款拆解</span>
              <div class="grow" />
              <button class="ui-btn" @click="toast('已导出 PNG（示例）')">导出图表</button>
            </div>

            <section class="ui-panel">
              <div class="ui-panel-head">
                <h2>多图联动</h2>
                <span class="hint">框选上图的区间 → 下图同步过滤</span>
              </div>
              <div class="ui-panel-body ui-chart"><EChart :build="brushTop" /></div>
              <div class="ui-panel-body ui-chart short" style="border-top: 1px solid var(--line-soft)">
                <EChart :build="brushBottom" />
              </div>
            </section>

            <section class="ui-panel">
              <div class="ui-panel-head">
                <h2>对标账号拆解</h2>
                <span class="hint">点行查看详情</span>
              </div>
              <table class="ui-table">
                <thead>
                  <tr>
                    <th>账号</th><th>平台</th>
                    <th style="text-align: right">内容数</th>
                    <th style="text-align: right">中位播放</th>
                    <th style="text-align: right">爆款数</th>
                    <th style="text-align: right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="c in benchmark" :key="c.name">
                    <td class="strong">{{ c.name }}</td>
                    <td>{{ c.platform }}</td>
                    <td class="num">{{ c.count }}</td>
                    <td class="num">{{ c.median }}</td>
                    <td class="num">{{ c.viral }}</td>
                    <td style="text-align: right">
                      <button class="ui-btn ghost sm" @click="toast(`打开「${c.name}」拆解报告（示例）`)">拆解</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </section>
          </template>

          <!-- ============ 关注博主 ============ -->
          <template v-else-if="page === 'creators'">
            <div class="ui-head">
              <h1>关注博主</h1>
              <span class="sub">真实头像与昵称 · 点击跳转主页</span>
              <div class="grow" />
              <button class="ui-btn" :disabled="loading" @click="doRefresh(false)">
                {{ loading ? '拉取中…' : '刷新关注列表' }}
              </button>
              <button class="ui-btn primary" :disabled="loading" @click="doRefresh(true)">强制刷新</button>
            </div>

            <div class="ui-tabs">
              <button v-for="t in ['抖音 3', 'B站 3']" :key="t" class="ui-tab" :class="{ on: t.startsWith(plat) }" @click="plat = t.slice(0, 2)">
                {{ t }}
              </button>
            </div>
            <div style="display: flex; gap: 8px">
              <button v-for="a in ['抖音·主号', '抖音·备用号']" :key="a"
                class="ui-chip" :class="{ on: acct === a }" @click="acct = a">{{ a }}</button>
            </div>

            <div v-if="cooling" class="ui-callout warn">
              平台风控冷却中，当前展示缓存数据（约 29 分钟后可再拉取）
            </div>

            <div v-if="loading" class="ui-grid">
              <div v-for="i in 3" :key="i" class="ui-skel" style="height: 168px" />
            </div>
            <div v-else class="ui-grid">
              <article v-for="c in visibleCreators" :key="c.name" class="ui-card">
                <div class="row">
                  <div class="ava" :style="{ background: c.avatarBg }">{{ c.name.slice(0, 1) }}</div>
                  <div style="min-width: 0">
                    <div class="name">
                      <a href="#" @click.prevent="toast(`打开 ${c.name} 主页（示例）`)">{{ c.name }}</a>
                      <span v-if="c.verified" class="ui-badge">认证</span>
                    </div>
                    <div class="stats">粉丝 {{ c.fans }} · 获赞 {{ c.likes }}</div>
                  </div>
                  <div class="grow" />
                  <button class="ui-icon-btn" title="立即同步" @click="toast(`开始同步 ${c.name}…`)">↻</button>
                </div>
                <p class="sig">{{ c.sig }}</p>
                <div class="foot">
                  <span class="ui-toggle">
                    <span>开启同步</span>
                    <button class="ui-switch" :class="{ on: c.on }" @click="c.on = !c.on"><i /></button>
                  </span>
                  <span v-if="c.on" class="ui-toggle">
                    <span>全量</span>
                    <button class="ui-switch sm" :class="{ on: c.full }" @click="c.full = !c.full"><i /></button>
                  </span>
                  <span v-else class="ui-badge">仅新发布</span>
                </div>
              </article>
            </div>
          </template>

          <!-- ============ 关键词采集 ============ -->
          <template v-else-if="page === 'crawl'">
            <div class="ui-head">
              <h1>关键词采集</h1>
              <span class="sub">按关键词发现作品 · 支持门槛触发评论抓取</span>
              <div class="grow" />
              <button class="ui-btn primary" @click="toast('已创建采集任务：露营装备（抖音）')">新建采集</button>
            </div>

            <section class="ui-panel">
              <div class="ui-panel-head">
                <h2>采集条件</h2>
                <span class="hint">门槛随账号规模动态计算，避免硬编码阈值</span>
              </div>
              <div class="ui-form">
                <label>关键词</label>
                <div style="display: flex; gap: 8px; flex-wrap: wrap">
                  <input class="ui-input" value="露营装备" style="width: 220px" />
                  <button class="ui-btn ghost sm" @click="toast('已添加关键词「户外电源」')">+ 加词</button>
                </div>
                <label>平台</label>
                <div class="ui-seg" style="align-self: start">
                  <button v-for="p in ['抖音', 'B站', '快手', '知乎']" :key="p"
                    :class="{ on: crawlPlats.includes(p) }" @click="toggleCrawlPlat(p)">{{ p }}</button>
                </div>
                <label>评论触发门槛</label>
                <div style="display: flex; align-items: center; gap: 10px">
                  <span class="ui-badge acc">点赞 ≥ {{ crawLGate.like }}万 且 评论 ≥ {{ crawLGate.comment }}</span>
                  <button class="ui-btn ghost sm" @click="tuneGate">按账号体量智能调整</button>
                </div>
              </div>
            </section>

            <section class="ui-panel">
              <div class="ui-panel-head">
                <h2>命中作品</h2>
                <span class="hint">高赞评论优先入库，其余仅记数量</span>
                <div class="grow" />
                <span class="ui-badge">{{ crawlHits.length }} 条</span>
              </div>
              <table class="ui-table">
                <thead>
                  <tr>
                    <th>作品</th><th>平台</th>
                    <th style="text-align: right">点赞</th>
                    <th style="text-align: right">评论</th>
                    <th style="text-align: right">抓评论</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="h in crawlHits" :key="h.title">
                    <td class="strong">{{ h.title }}</td>
                    <td>{{ h.plat }}</td>
                    <td class="num">{{ h.like }}</td>
                    <td class="num">{{ h.comment }}</td>
                    <td style="text-align: right">
                      <span v-if="h.hit" class="ui-badge acc">触发 · 已抓 {{ h.got }} 条高赞</span>
                      <span v-else class="ui-badge">未达门槛 · 仅数量</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </section>
          </template>

          <!-- ============ 订阅同步 ============ -->
          <template v-else-if="page === 'subscriptions'">
            <div class="ui-head">
              <h1>订阅同步</h1>
              <span class="sub">公众号订阅已确认官方关闭，仅保留博主订阅</span>
              <div class="grow" />
              <button class="ui-btn" @click="toast('已同步全部开启订阅的账号（示例）')">全部同步</button>
            </div>

            <div class="ui-callout warn">公众号订阅同步能力已被官方关闭，相关入口已下线，不影响博主订阅与关键词采集。</div>

            <section class="ui-panel">
              <div class="ui-panel-head">
                <h2>订阅任务</h2>
                <span class="hint">开启后按策略拉取，命中缓存不重复请求</span>
              </div>
              <table class="ui-table">
                <thead>
                  <tr>
                    <th>账号</th><th>平台</th><th>上次同步</th>
                    <th style="text-align: right">新增</th><th style="text-align: right">同步</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="s in subs" :key="s.name">
                    <td class="strong">{{ s.name }}</td>
                    <td>{{ s.plat }}</td>
                    <td>{{ s.last }}</td>
                    <td class="num">{{ s.add }}</td>
                    <td style="text-align: right">
                      <button class="ui-switch" :class="{ on: s.on }" @click="s.on = !s.on"><i /></button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </section>
          </template>

          <!-- ============ 素材库 ============ -->
          <template v-else-if="page === 'assets'">
            <div class="ui-head">
              <h1>素材库</h1>
              <span class="sub">共 41 条 · 已入库</span>
              <div class="grow" />
              <div class="ui-seg">
                <button v-for="v in ['全部','视频','文章','表格']" :key="v" :class="{ on: assetView === v }" @click="assetView = v">{{ v }}</button>
              </div>
            </div>

            <!-- 密度切换：卡片 / 表格 两种视图 -->
            <div v-if="assetView !== '表格'" class="ui-grid">
              <article v-for="a in assets" :key="a.title" class="ui-card asset">
                <div class="thumb" :style="{ background: a.tone }">
                  <span>{{ a.platform }}</span>
                  <span class="dur">{{ a.dur }}</span>
                </div>
                <div class="a-title">{{ a.title }}</div>
                <div class="a-meta">{{ a.author }}</div>
                <div class="a-nums">
                  <span>播放 {{ a.play }}</span><span>赞 {{ a.like }}</span>
                </div>
              </article>
            </div>
            <section v-else class="ui-panel">
              <table class="ui-table">
                <thead><tr><th>标题</th><th>平台</th><th>作者</th><th style="text-align: right">播放</th></tr></thead>
                <tbody>
                  <tr v-for="a in assets" :key="a.title">
                    <td class="strong">{{ a.title }}</td><td>{{ a.platform }}</td><td>{{ a.author }}</td>
                    <td class="num">{{ a.play }}</td>
                  </tr>
                </tbody>
              </table>
            </section>
          </template>

          <!-- ============ 处理队列 ============ -->
          <template v-else-if="page === 'pipeline'">
            <div class="ui-head">
              <h1>处理队列</h1>
              <span class="sub">下载 / 转录 / 视频理解</span>
              <div class="grow" />
              <button class="ui-btn danger" @click="toast('已重试 2 个失败任务')">重试失败</button>
            </div>
            <div class="ui-metrics" style="grid-template-columns: repeat(3, 1fr)">
              <div class="ui-metric"><div class="k">运行中</div><div class="v">4</div><div class="d">下载 2 · 转录 1 · 理解 1</div></div>
              <div class="ui-metric"><div class="k">排队</div><div class="v">3</div><div class="d">预计 6 分钟</div></div>
              <div class="ui-metric alert"><div class="k"><span class="ui-dot bad" />失败</div><div class="v">2</div><div class="d">可一键重试</div></div>
            </div>
            <section class="ui-panel">
              <div class="ui-panel-head"><h2>任务时间线</h2></div>
              <div class="ui-panel-body ui-timeline">
                <div v-for="t in tasks" :key="t.id" class="tl-item">
                  <span class="ui-dot" :class="t.tone" />
                  <div>
                    <div class="tl-t">{{ t.title }}</div>
                    <div class="tl-m">{{ t.meta }}</div>
                  </div>
                  <div class="grow" />
                  <span class="ui-badge" :class="t.tone">{{ t.status }}</span>
                </div>
              </div>
            </section>
          </template>

          <!-- ============ 组件与图表 ============ -->
          <template v-else-if="page === 'charts'">
            <div class="ui-head">
              <h1>组件与图表</h1>
              <span class="sub">ECharts 6 · 工作台会用到的全部交互型图表</span>
              <div class="grow" />
              <button class="ui-btn" @click="toast('已导出当前图表数据（示例）')">导出数据</button>
            </div>

            <!-- 图表密度切换：证明分段控件真的在切换内容 -->
            <div class="ui-seg" style="align-self: flex-start">
              <button v-for="c in chartTabs" :key="c.k" :class="{ on: chartTab === c.k }" @click="chartTab = c.k">
                {{ c.label }}
              </button>
            </div>

            <div class="ui-grid wide">
              <!-- 热力图：一周 × 时段 的抓取密度 -->
              <section v-show="chartTab === 'all' || chartTab === 'density'" class="ui-panel">
                <div class="ui-panel-head">
                  <h2>抓取密度热力图</h2>
                  <span class="hint">周 × 时段 · hover 看每分钟条数</span>
                </div>
                <div class="ui-panel-body ui-chart"><EChart :build="heatChart" /></div>
              </section>

              <!-- 漏斗：采集 → 入库 → 转录 → 理解 -->
              <section v-show="chartTab === 'all' || chartTab === 'funnel'" class="ui-panel">
                <div class="ui-panel-head">
                  <h2>素材加工漏斗</h2>
                  <span class="hint">点条目高亮，看每级损耗</span>
                </div>
                <div class="ui-panel-body ui-chart"><EChart :build="funnelChart" /></div>
              </section>

              <!-- 雷达：账号内容力五维 -->
              <section v-show="chartTab === 'all' || chartTab === 'radar'" class="ui-panel">
                <div class="ui-panel-head">
                  <h2>账号内容力雷达</h2>
                  <span class="hint">点击图例可切换对比账号</span>
                </div>
                <div class="ui-panel-body ui-chart"><EChart :build="radarChart" /></div>
              </section>

              <!-- 日历热力：近半年发布节奏 -->
              <section v-show="chartTab === 'all' || chartTab === 'calendar'" class="ui-panel">
                <div class="ui-panel-head">
                  <h2>发布节奏日历</h2>
                  <span class="hint">颜色越深当日发布越多</span>
                </div>
                <div class="ui-panel-body ui-chart"><EChart :build="calendarChart" /></div>
              </section>

              <!-- 桑基：素材流向 -->
              <section v-show="chartTab === 'all' || chartTab === 'sankey'" class="ui-panel span-2">
                <div class="ui-panel-head">
                  <h2>素材流向桑基图</h2>
                  <span class="hint">平台 → 加工 → 去向 · 节点可拖拽</span>
                </div>
                <div class="ui-panel-body ui-chart"><EChart :build="sankeyChart" /></div>
              </section>

              <!-- 仪表盘：风控健康度 -->
              <section v-show="chartTab === 'all' || chartTab === 'gauge'" class="ui-panel">
                <div class="ui-panel-head">
                  <h2>风控健康度</h2>
                  <span class="hint">低于 60 自动降速</span>
                </div>
                <div class="ui-panel-body ui-chart"><EChart :build="gaugeChart" /></div>
              </section>
            </div>

            <p class="ui-note">
              以上均为真实 ECharts 实例（非图片）：热力图、漏斗、雷达、日历、桑基、仪表盘。
              加上总览页的折线/柱状/环形/散点/迷你走势，看板所需的图表类型已基本齐全。
            </p>
          </template>

          <!-- ============ 设置 ============ -->
          <template v-else>
            <div class="ui-head">
              <h1>系统设置</h1>
              <span class="sub">模型 / 加工 / 提示词</span>
            </div>
            <section class="ui-panel">
              <div class="ui-panel-head"><h2>视频理解</h2></div>
              <div class="ui-form">
                <label>视觉模型</label>
                <input class="ui-input" value="qwen3.8-flash" />
                <label>低置信度阈值</label>
                <div><input class="ui-input" value="0.55" style="width: 120px" /><span class="ui-help" style="margin-left: 8px">低于则标记待复核</span></div>
                <label>分档</label>
                <div class="ui-seg" style="align-self: start">
                  <button :class="{ on: tier === 'basic' }" @click="tier = 'basic'">普通（转录）</button>
                  <button :class="{ on: tier === 'advanced' }" @click="tier = 'advanced'">高级（视频理解）</button>
                </div>
                <label>类型提示词</label>
                <textarea class="ui-input" rows="4">{{ prompt }}</textarea>
              </div>
            </section>
          </template>

          <p class="ui-note">
            这是可交互样张：按钮、开关、分段控件、图表缩放/图例/框选都可以点。
            确定方向后，我会把它沉淀为令牌 + 组件库，再逐页替换。
          </p>
        </div>
      </div>
    </div>

    <!-- Toast 反馈，证明交互是活的 -->
    <div class="ui-toasts">
      <div v-for="t in toasts" :key="t.id" class="ui-toast">{{ t.msg }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, reactive, ref } from 'vue'
// Load the sample's full chart capability only on this route.
const EChart = defineAsyncComponent(async () => {
  const { ensureAllCharts } = await import('../components/charts/echarts-all')
  ensureAllCharts()
  return (await import('../components/charts/EChart.vue')).default
})
import '../styles/ui.css'

const pages = [
  { key: 'overview', label: '总览' },
  { key: 'analytics', label: '数据看板' },
  { key: 'charts', label: '组件与图表' },
  { key: 'creators', label: '关注博主' },
  { key: 'crawl', label: '关键词采集' },
  { key: 'subscriptions', label: '订阅同步' },
  { key: 'assets', label: '素材库' },
  { key: 'pipeline', label: '处理队列' },
  { key: 'settings', label: '设置' },
]
const dirs = [
  { key: 'grotesk', label: 'V1 · 瑞士排版' },
  { key: 'nocturne', label: 'V2 · 深夜控制台' },
  { key: 'bauhaus', label: 'V3 · 包豪斯' },
]
const page = ref('overview')
const dir = ref<'grotesk' | 'nocturne' | 'bauhaus'>('grotesk')

const pageTitle = computed(() => pages.find((p) => p.key === page.value)?.label ?? '')

const nav = [
  { t: '总览', key: 'overview' },
  { t: '账号中心', key: 'creators' },
  { t: '关键词采集', key: 'crawl' },
  { t: '订阅同步', key: 'subscriptions', k: '6' },
  { t: '关注博主', key: 'creators' },
  { t: '素材库', key: 'assets' },
  { t: '处理队列', key: 'pipeline', k: '7' },
  { t: '数据看板', key: 'analytics' },
  { t: '组件与图表', key: 'charts' },
  { t: '系统设置', key: 'settings' },
]

/* ---------- toast ---------- */
const toasts = reactive<{ id: number; msg: string }[]>([])
let tid = 0
function toast(msg: string) {
  const id = ++tid
  toasts.push({ id, msg })
  setTimeout(() => {
    const i = toasts.findIndex((t) => t.id === id)
    if (i >= 0) toasts.splice(i, 1)
  }, 2400)
}

/* ---------- 总览 ---------- */
const ranges = ['24 小时', '7 天', '30 天']
const range = ref('7 天')
const metricMode = ref<'count' | 'play'>('count')
const m = { crawled: 41, assets: 38, pending: 7, failed: 2 }

const days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const counts = [18, 26, 22, 34, 41, 30, 36]
const plays = [62, 88, 74, 121, 168, 132, 149]

/* 迷你走势：sparkline（无轴、无交互，纯形态） */
function sparkline(data: number[], color: string) {
  return (t: Record<string, string>) => ({
    grid: { left: 2, right: 2, top: 6, bottom: 2 },
    xAxis: { type: 'category', show: false, data: days },
    yAxis: { type: 'value', show: false, scale: true },
    tooltip: { show: false },
    series: [{
      type: 'line', data, smooth: 0.35, symbol: 'none',
      lineStyle: { width: 1.6, color: color === 'ok' ? t['--ok'] : color === 'bad' ? t['--danger'] : t['--muted'] },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: color === 'ok' ? t['--ok-soft'] : color === 'bad' ? t['--danger-soft'] : t['--surface-2'] },
            { offset: 1, color: 'transparent' },
          ],
        },
      },
    }],
  })
}
const sparkUp = sparkline(counts, 'ok')
const sparkUp2 = sparkline([30, 28, 33, 36, 34, 37, 38], 'ok')
const sparkFlat = sparkline([6, 8, 5, 9, 7, 8, 7], 'muted')
const sparkDown = sparkline([3, 2, 4, 3, 2, 3, 2], 'bad')

/* 主趋势图 */
const trendChart = computed(() => (t: Record<string, string>) => ({
  textStyle: { fontFamily: t['--font-sans'] || 'inherit' },
  color: [t['--accent'], t['--accent-2'], t['--ok']],
  grid: { left: 8, right: 14, top: 34, bottom: 34, containLabel: true },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'cross', label: { backgroundColor: t['--ink'] } },
    backgroundColor: t['--surface'],
    borderColor: t['--line'],
    textStyle: { color: t['--ink'], fontSize: 12 },
  },
  legend: { right: 0, top: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10, textStyle: { color: t['--ink-2'] } },
  xAxis: {
    type: 'category', data: days, boundaryGap: false,
    axisLine: { lineStyle: { color: t['--line'] } },
    axisTick: { show: false },
    axisLabel: { color: t['--muted'] },
  },
  yAxis: {
    type: 'value',
    splitLine: { lineStyle: { color: t['--chart-grid'] } },
    axisLabel: { color: t['--muted'] },
  },
  dataZoom: [
    { type: 'inside', start: 0, end: 100 },
    { type: 'slider', height: 14, bottom: 4, borderColor: 'transparent',
      backgroundColor: t['--surface-2'], fillerColor: t['--accent-soft'],
      handleStyle: { color: t['--accent'] }, textStyle: { color: t['--muted'] } },
  ],
  series:
    metricMode.value === 'count'
      ? [
          { name: '抓取', type: 'line', smooth: 0.3, symbolSize: 6, data: counts,
            lineStyle: { width: 2.5 }, areaStyle: { opacity: 0.14 } },
          { name: '入库', type: 'line', smooth: 0.3, symbolSize: 6,
            data: counts.map((v) => Math.max(0, v - 3)), lineStyle: { width: 2 }, areaStyle: { opacity: 0.10 } },
        ]
      : [
          { name: '播放量(万)', type: 'line', smooth: 0.3, symbolSize: 6, data: plays, lineStyle: { width: 2.5 } },
        ],
}))

/* 平台占比：环形 */
const shareChart = (t: Record<string, string>) => ({
  textStyle: { fontFamily: t['--font-sans'] },
  color: [t['--accent'], t['--accent-2'], t['--ok'], t['--warn']],
  tooltip: { trigger: 'item', backgroundColor: t['--surface'], borderColor: t['--line'], textStyle: { color: t['--ink'] } },
  legend: { bottom: 0, icon: 'circle', itemWidth: 9, itemHeight: 9, textStyle: { color: t['--ink-2'] } },
  series: [{
    type: 'pie', radius: ['52%', '74%'], center: ['50%', '44%'], avoidLabelOverlap: true,
    itemStyle: { borderColor: t['--surface'], borderWidth: 2 },
    label: { show: false }, labelLine: { show: false },
    data: [
      { name: '抖音', value: 21 }, { name: 'B站', value: 12 },
      { name: '小红书', value: 5 }, { name: '微信公众号', value: 3 },
    ],
  }],
  graphic: [{
    type: 'text', left: 'center', top: '38%',
    style: { text: '41', fontSize: 26, fontWeight: 700, fill: t['--ink'], fontFamily: t['--font-mono'] },
  }, {
    type: 'text', left: 'center', top: '50%',
    style: { text: '总入库', fontSize: 11, fill: t['--muted'] },
  }],
})

/* 爆款四象限散点 */
const quadrantChart = (t: Record<string, string>) => ({
  textStyle: { fontFamily: t['--font-sans'] },
  grid: { left: 10, right: 18, top: 18, bottom: 30, containLabel: true },
  tooltip: {
    backgroundColor: t['--surface'], borderColor: t['--line'], textStyle: { color: t['--ink'] },
    formatter: (p: any) => `${p.data[3]}<br/>播放 ${p.data[0]}万 · 互动率 ${p.data[1]}%`,
  },
  xAxis: {
    type: 'value', name: '播放(万)', nameTextStyle: { color: t['--muted'] },
    splitLine: { lineStyle: { color: t['--chart-grid'] } }, axisLabel: { color: t['--muted'] },
  },
  yAxis: {
    type: 'value', name: '互动率(%)', nameTextStyle: { color: t['--muted'] },
    splitLine: { lineStyle: { color: t['--chart-grid'] } }, axisLabel: { color: t['--muted'] },
  },
  series: [{
    type: 'scatter', symbolSize: (d: number[]) => Math.max(10, Math.min(38, d[2])),
    itemStyle: { color: t['--accent'], opacity: 0.72, borderColor: t['--surface'], borderWidth: 1 },
    markLine: {
      silent: true, symbol: 'none',
      lineStyle: { color: t['--line'], type: 'dashed' },
      label: { color: t['--muted'], fontSize: 10 },
      data: [{ xAxis: 60, label: { formatter: '播放中位' } }, { yAxis: 6, label: { formatter: '互动中位' } }],
    },
    data: [
      [182, 8.2, 2400, '3分钟红烧肉'], [96, 5.4, 1200, '露营装备'],
      [74, 4.9, 900, '居家燃脂'], [41, 6.8, 700, '接私活的坑'],
      [28, 9.1, 500, '空气炸锅蛋挞'], [12, 3.2, 300, '新手剪辑'],
      [58, 2.4, 800, '一周好物'], [156, 3.1, 1600, '夜市炒面'],
    ],
  }],
})

/* 多图联动：brush 框选 */
const brushTop = (t: Record<string, string>) => ({
  textStyle: { fontFamily: t['--font-sans'] },
  grid: { left: 8, right: 16, top: 24, bottom: 22, containLabel: true },
  tooltip: { trigger: 'axis', backgroundColor: t['--surface'], borderColor: t['--line'], textStyle: { color: t['--ink'] } },
  brush: { xAxisIndex: 0, brushLink: [0, 1], throttleType: 'debounce', throttleDelay: 200 },
  xAxis: { type: 'category', data: days, axisLabel: { color: t['--muted'] }, axisLine: { lineStyle: { color: t['--line'] } }, axisTick: { show: false } },
  yAxis: { type: 'value', splitLine: { lineStyle: { color: t['--chart-grid'] } }, axisLabel: { color: t['--muted'] } },
  series: [{ type: 'bar', name: '新增', data: counts, itemStyle: { color: t['--accent'], borderRadius: [3, 3, 0, 0] }, barMaxWidth: 26 }],
})

const brushBottom = (t: Record<string, string>) => ({
  textStyle: { fontFamily: t['--font-sans'] },
  grid: { left: 8, right: 16, top: 16, bottom: 18, containLabel: true },
  tooltip: { trigger: 'axis', backgroundColor: t['--surface'], borderColor: t['--line'], textStyle: { color: t['--ink'] } },
  xAxis: { type: 'category', data: days, axisLabel: { color: t['--muted'] }, axisLine: { lineStyle: { color: t['--line'] } }, axisTick: { show: false } },
  yAxis: { type: 'value', splitLine: { lineStyle: { color: t['--chart-grid'] } }, axisLabel: { color: t['--muted'] } },
  series: [{ type: 'line', name: '播放量(万)', smooth: 0.3, data: plays, symbol: 'none',
    lineStyle: { width: 2, color: t['--accent-2'] },
    areaStyle: { opacity: 0.12, color: t['--accent-2'] } }],
})

const benchmark = [
  { name: '老饭骨·王刚', platform: '抖音', count: 128, median: '86万', viral: 9 },
  { name: '影视飓风', platform: 'B站', count: 96, median: '52万', viral: 6 },
  { name: '帕梅拉·健身日记', platform: '抖音', count: 74, median: '38万', viral: 4 },
  { name: '程序员鱼皮', platform: 'B站', count: 63, median: '21万', viral: 2 },
]

/* ---------- 关注博主 ---------- */
const plat = ref('抖音')
const acct = ref('抖音·主号')
const loading = ref(false)
const cooling = ref(false)
const creators = reactive([
  { name: '老饭骨·王刚', fans: '860万', likes: '2580万', sig: '传承中华美食，专注家常菜教学', verified: true, on: true, full: true, avatarBg: 'oklch(0.55 0.20 28)', plat: '抖音' },
  { name: '帕梅拉·健身日记', fans: '320万', likes: '1600万', sig: '每天 10 分钟，跟着练就有效果', verified: true, on: false, full: false, avatarBg: 'oklch(0.48 0.17 258)', plat: '抖音' },
  { name: '露营的阿远', fans: '46万', likes: '138万', sig: '周末去哪玩？带你找宝藏露营地', verified: false, on: false, full: false, avatarBg: 'oklch(0.58 0.13 158)', plat: '抖音' },
  { name: '影视飓风', fans: '510万', likes: '4080万', sig: '用影像讲故事的技术宅', verified: true, on: true, full: false, avatarBg: 'oklch(0.62 0.19 320)', plat: 'B站' },
  { name: '程序员鱼皮', fans: '82万', likes: '410万', sig: '编程学习 | 求职干货 | 项目实战', verified: true, on: false, full: false, avatarBg: 'oklch(0.52 0.215 262)', plat: 'B站' },
  { name: '科技美学', fans: '240万', likes: '1680万', sig: '独立客观的数码评测', verified: true, on: false, full: false, avatarBg: 'oklch(0.72 0.15 72)', plat: 'B站' },
])
const visibleCreators = computed(() =>
  creators.filter((c) => (plat.value === '抖音' ? c.plat === '抖音' : c.plat === 'B站')),
)
function doRefresh(force: boolean) {
  loading.value = true
  cooling.value = false
  setTimeout(() => {
    loading.value = false
    if (!force) {
      toast('命中 6 小时缓存，未请求平台（防频繁拉取）')
    } else {
      cooling.value = true
      toast('平台风控冷却中，展示缓存数据')
    }
  }, 900)
}

/* ---------- 素材库 ---------- */
const assetView = ref('全部')
const crawlPlats = ref<string[]>(['抖音', 'B站'])
function toggleCrawlPlat(p: string) {
  const i = crawlPlats.value.indexOf(p)
  if (i >= 0) crawlPlats.value.splice(i, 1)
  else crawlPlats.value.push(p)
}
const crawLGate = reactive({ like: 5, comment: 800 })
function tuneGate() {
  crawLGate.like = Math.round((3 + Math.random() * 5) * 10) / 10
  crawLGate.comment = Math.round(300 + Math.random() * 1200)
  toast(`门槛已按账号体量调整为 点赞 ≥ ${crawLGate.like}万 / 评论 ≥ ${crawLGate.comment}`)
}
const crawlHits = reactive([
  { title: '露营装备怎么选？一次讲清楚', plat: 'B站', like: '5.1万', comment: '2140', hit: true, got: 186 },
  { title: '三天两夜无人区露营', plat: '抖音', like: '18.7万', comment: '9320', hit: true, got: 240 },
  { title: '新手野炊避坑指南', plat: '抖音', like: '1.2万', comment: '410', hit: false, got: 0 },
  { title: '轻量化帐篷横评', plat: 'B站', like: '8600', comment: '260', hit: false, got: 0 },
])
const subs = reactive([
  { name: '老饭骨·王刚', plat: '抖音', last: '2 小时前', add: 3, on: true },
  { name: '影视飓风', plat: 'B站', last: '5 小时前', add: 1, on: true },
  { name: '帕梅拉·健身日记', plat: '抖音', last: '昨天', add: 0, on: false },
  { name: '程序员鱼皮', plat: 'B站', last: '3 天前', add: 2, on: true },
])
const assets = [
  { title: '3分钟教你做出饭店级红烧肉', platform: '抖音', author: '老饭骨·王刚', play: '182万', like: '12.4万', dur: '03:12', tone: 'linear-gradient(135deg, oklch(0.55 0.20 28), oklch(0.62 0.19 320))' },
  { title: '露营装备怎么选？一次讲清楚', platform: 'B站', author: '影视飓风', play: '96万', like: '5.1万', dur: '12:40', tone: 'linear-gradient(135deg, oklch(0.48 0.17 258), oklch(0.52 0.215 262))' },
  { title: '每天10分钟居家燃脂', platform: '抖音', author: '帕梅拉', play: '74万', like: '3.8万', dur: '10:05', tone: 'linear-gradient(135deg, oklch(0.58 0.13 158), oklch(0.72 0.15 72))' },
  { title: '程序员接私活的几个坑', platform: 'B站', author: '程序员鱼皮', play: '41万', like: '2.2万', dur: '08:30', tone: 'linear-gradient(135deg, oklch(0.52 0.215 262), oklch(0.58 0.13 158))' },
]

/* ---------- 队列 ---------- */
const tasks = [
  { id: 1, title: '转录 · 3分钟红烧肉', meta: '抖音 worker-1 · 已用 42s', status: '运行中', tone: '' },
  { id: 2, title: '视频理解 · 露营装备', meta: 'B站 · qwen3.8-flash', status: '运行中', tone: '' },
  { id: 3, title: '下载 · 居家燃脂', meta: '排队第 1 位', status: '排队', tone: 'warn' },
  { id: 4, title: '下载 · 夜市炒面', meta: 'Cookie 失效，需要重新登录', status: '失败', tone: 'bad' },
  { id: 5, title: '转录 · 接私活的坑', meta: '完成 · 4,182 字', status: '完成', tone: 'ok' },
]

/* ---------- 设置 ---------- */
const tier = ref('advanced')
const prompt = ref('你是视频内容分析师。根据视频类型选择分析维度…')

/* ---------- 组件与图表 ---------- */
const chartTabs = [
  { k: 'all', label: '全部' },
  { k: 'density', label: '热力' },
  { k: 'funnel', label: '漏斗' },
  { k: 'radar', label: '雷达' },
  { k: 'calendar', label: '日历' },
  { k: 'sankey', label: '桑基' },
  { k: 'gauge', label: '仪表' },
]
const chartTab = ref('all')

const heatDays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const heatHours = ['00', '04', '08', '12', '16', '20']
const heatData: [number, number, number][] = []
for (let h = 0; h < heatHours.length; h++) {
  for (let d = 0; d < heatDays.length; d++) {
    const peak = h >= 3 && h <= 5 ? 18 : h === 2 ? 10 : 4
    heatData.push([d, h, Math.max(1, Math.round(peak * (0.4 + 0.6 * Math.random())))])
  }
}
const heatChart = (t: Record<string, string>) => ({
  tooltip: {
    position: 'top',
    backgroundColor: t['--surface'],
    borderColor: t['--line'],
    textStyle: { color: t['--ink'] },
  },
  grid: { left: 50, right: 20, top: 16, bottom: 40 },
  xAxis: {
    type: 'category',
    data: heatDays,
    axisLabel: { color: t['--muted'] },
    axisLine: { lineStyle: { color: t['--line'] } },
    splitArea: { show: false },
  },
  yAxis: {
    type: 'category',
    data: heatHours,
    axisLabel: { color: t['--muted'] },
    axisLine: { lineStyle: { color: t['--line'] } },
  },
  visualMap: {
    min: 0,
    max: 20,
    calculable: true,
    orient: 'horizontal',
    left: 'center',
    bottom: 0,
    itemHeight: 100,
    textStyle: { color: t['--muted'], fontSize: 10 },
    inRange: { color: [t['--surface-2'], t['--accent-soft'], t['--accent']] },
  },
  series: [
    {
      type: 'heatmap',
      data: heatData,
      itemStyle: { borderColor: t['--surface'], borderWidth: 2 },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'oklch(0 0 0 / 0.3)' } },
    },
  ],
})

const funnelChart = (t: Record<string, string>) => ({
  tooltip: {
    trigger: 'item',
    backgroundColor: t['--surface'],
    borderColor: t['--line'],
    textStyle: { color: t['--ink'] },
  },
  series: [
    {
      type: 'funnel',
      left: '8%',
      right: '8%',
      top: 16,
      bottom: 16,
      minSize: '22%',
      sort: 'descending',
      gap: 4,
      label: { color: t['--ink'], fontSize: 11 },
      itemStyle: { borderColor: t['--surface'], borderWidth: 1 },
      data: [
        { value: 412, name: '采集 412' },
        { value: 318, name: '下载 318' },
        { value: 264, name: '入库 264' },
        { value: 187, name: '转录 187' },
        { value: 62, name: '视频理解 62' },
      ],
      color: [t['--accent'], t['--accent-2'], t['--ok'], t['--warn'], t['--danger']],
    },
  ],
})

const radarChart = (t: Record<string, string>) => ({
  tooltip: { backgroundColor: t['--surface'], borderColor: t['--line'], textStyle: { color: t['--ink'] } },
  legend: { bottom: 0, icon: 'circle', itemWidth: 9, itemHeight: 9, textStyle: { color: t['--ink-2'] } },
  radar: {
    center: ['50%', '46%'],
    radius: '62%',
    indicator: [
      { name: '更新频率', max: 100 },
      { name: '完播率', max: 100 },
      { name: '互动率', max: 100 },
      { name: '涨粉力', max: 100 },
      { name: '选题广度', max: 100 },
    ],
    axisName: { color: t['--muted'], fontSize: 11 },
    splitLine: { lineStyle: { color: t['--chart-grid'] } },
    axisLine: { lineStyle: { color: t['--chart-grid'] } },
    splitArea: { areaStyle: { color: ['transparent'] } },
  },
  series: [
    {
      type: 'radar',
      data: [
        { value: [88, 72, 81, 66, 74], name: '老饭骨·王刚' },
        { value: [62, 88, 70, 80, 58], name: '影视飓风' },
      ],
      areaStyle: { opacity: 0.14 },
      lineStyle: { width: 2 },
      symbolSize: 4,
    },
  ],
  color: [t['--accent'], t['--accent-2']],
})

const calendarChart = (t: Record<string, string>) => {
  const data: [string, number][] = []
  const end = new Date('2026-10-06')
  for (let i = 180; i >= 0; i--) {
    const d = new Date(end)
    d.setDate(end.getDate() - i)
    const key = d.toISOString().slice(0, 10)
    const dow = d.getDay()
    const base = dow === 0 || dow === 6 ? 1 : 3
    data.push([key, Math.round(base + Math.random() * 4)])
  }
  return {
    tooltip: {
      backgroundColor: t['--surface'],
      borderColor: t['--line'],
      textStyle: { color: t['--ink'] },
    },
    visualMap: {
      min: 0,
      max: 7,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      itemHeight: 90,
      textStyle: { color: t['--muted'], fontSize: 10 },
      inRange: { color: [t['--surface-2'], t['--accent-soft'], t['--accent']] },
    },
    calendar: {
      top: 30,
      left: 40,
      right: 20,
      cellSize: ['auto', 13],
      range: '2026-04-09',
      itemStyle: { borderColor: t['--surface'], borderWidth: 2, color: 'transparent' },
      splitLine: { lineStyle: { color: t['--line-soft'] } },
      yearLabel: { show: false },
      dayLabel: { color: t['--muted'], fontSize: 10, nameMap: 'ZH' },
      monthLabel: { color: t['--ink-2'], fontSize: 11, nameMap: 'ZH' },
    },
    series: [{ type: 'heatmap', coordinateSystem: 'calendar', data }],
  }
}

const sankeyChart = (t: Record<string, string>) => ({
  tooltip: {
    trigger: 'item',
    backgroundColor: t['--surface'],
    borderColor: t['--line'],
    textStyle: { color: t['--ink'] },
  },
  series: [
    {
      type: 'sankey',
      left: 12,
      right: 12,
      top: 16,
      bottom: 16,
      nodeWidth: 14,
      nodeGap: 12,
      draggable: true,
      label: { color: t['--ink-2'], fontSize: 11 },
      lineStyle: { color: 'gradient', opacity: 0.28, curveness: 0.5 },
      emphasis: { focus: 'adjacency', lineStyle: { opacity: 0.5 } },
      data: [
        { name: '抖音' },
        { name: 'B站' },
        { name: '快手' },
        { name: '知乎' },
        { name: '原片直存' },
        { name: '音频转录' },
        { name: '视频理解' },
        { name: '素材库' },
        { name: '待复核' },
      ],
      links: [
        { source: '抖音', target: '原片直存', value: 96 },
        { source: '抖音', target: '视频理解', value: 42 },
        { source: 'B站', target: '音频转录', value: 68 },
        { source: 'B站', target: '视频理解', value: 20 },
        { source: '快手', target: '原片直存', value: 22 },
        { source: '知乎', target: '音频转录', value: 12 },
        { source: '原片直存', target: '素材库', value: 118 },
        { source: '音频转录', target: '素材库', value: 62 },
        { source: '音频转录', target: '待复核', value: 18 },
        { source: '视频理解', target: '素材库', value: 44 },
        { source: '视频理解', target: '待复核', value: 18 },
      ],
    },
  ],
  color: [t['--accent'], t['--accent-2'], t['--ok'], t['--warn'], t['--muted']],
})

const gaugeChart = (t: Record<string, string>) => ({
  series: [
    {
      type: 'gauge',
      startAngle: 210,
      endAngle: -30,
      min: 0,
      max: 100,
      radius: '86%',
      center: ['50%', '58%'],
      progress: { show: true, width: 16, itemStyle: { color: t['--ok'] } },
      axisLine: { lineStyle: { width: 16, color: [[1, t['--surface-3']]] } },
      pointer: { width: 4, length: '60%', itemStyle: { color: t['--ink'] } },
      axisTick: { distance: -20, length: 4, lineStyle: { color: t['--line'] } },
      splitLine: { distance: -22, length: 8, lineStyle: { color: t['--line'] } },
      axisLabel: { distance: -6, color: t['--muted'], fontSize: 10 },
      anchor: { show: false },
      title: { offsetCenter: [0, '34%'], color: t['--muted'], fontSize: 12 },
      detail: {
        valueAnimation: true,
        offsetCenter: [0, '8%'],
        fontSize: 30,
        fontFamily: 'var(--font-mono)',
        color: t['--ink'],
        formatter: '{value}',
      },
      data: [{ value: 82, name: '健康度 · 可正常拉取' }],
    },
  ],
})
</script>

<style scoped>
/* 距阵布局与页面专属细节 */
.ui-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--sp-3); }
[data-dir='grotesk'] .ui-metrics { gap: 1px; background: var(--line); border: 1px solid var(--line); }
[data-dir='nocturne'] .ui-metrics { gap: var(--sp-3); }
[data-dir='bauhaus'] .ui-metrics { gap: var(--sp-3); }
.ui-metric {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-lg);
  padding: var(--sp-4) var(--sp-4) 0; position: relative; overflow: hidden;
  display: flex; flex-direction: column; min-height: 132px;
}
[data-dir='grotesk'] .ui-metric { border: 0; border-radius: 0; }
[data-dir='bauhaus'] .ui-metric { box-shadow: var(--shadow-1); border-color: var(--ink); }
.ui-metric .k { font-size: var(--fs-sm); color: var(--muted); display: flex; align-items: center; gap: 6px; }
.ui-metric .v {
  font-size: var(--fs-3xl); font-weight: 700; font-family: var(--font-mono);
  letter-spacing: -0.03em; margin-top: 6px; color: var(--ink); line-height: 1;
}
.ui-metric .d { font-size: var(--fs-xs); color: var(--muted); margin-top: 8px; display: flex; align-items: center; gap: 6px; height: 18px; }
.ui-metric .spark { margin: auto -16px 0; height: 40px; }
[data-dir='grotesk'] .ui-metric .spark { margin: auto 0 0; }
.ui-metric.alert .v { color: var(--danger); }
[data-dir='nocturne'] .ui-metric { background: var(--surface); }

.ui-cols { display: grid; grid-template-columns: 1fr 1.25fr; gap: var(--sp-4); }
@media (max-width: 1180px) { .ui-cols { grid-template-columns: 1fr; } }

.ui-tabs { display: flex; gap: var(--sp-5); border-bottom: 1px solid var(--line); }
.ui-tab {
  padding: 8px 2px 10px; border: 0; background: none; font-family: inherit;
  font-size: var(--fs-base); color: var(--muted); cursor: pointer;
  border-bottom: 2px solid transparent; transition: all .15s ease-out;
}
.ui-tab.on { color: var(--ink); border-bottom-color: var(--accent); font-weight: 600; }
[data-dir='bauhaus'] .ui-tab.on { border-bottom-width: 3px; }

.ui-chip {
  padding: 4px 12px; border: 1px solid var(--line); border-radius: var(--radius-pill);
  background: var(--surface); color: var(--muted); font-family: inherit; font-size: var(--fs-sm); cursor: pointer;
}
.ui-chip.on { color: var(--accent); border-color: var(--accent-line); background: var(--accent-soft); font-weight: 600; }

.ui-callout { padding: 10px 14px; border-radius: var(--radius); font-size: var(--fs-md); border: 1px solid var(--line); }
.ui-callout.warn { background: var(--warn-soft); border-color: color-mix(in oklab, var(--warn) 45%, var(--line)); color: color-mix(in oklab, var(--warn) 72%, var(--ink)); }

.ui-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(288px, 1fr)); gap: var(--sp-3); }
.ui-grid.wide { grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); }
.ui-grid .span-2 { grid-column: span 2; }
@media (max-width: 900px) { .ui-grid .span-2 { grid-column: span 1; } }
.ui-card {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-lg);
  padding: var(--sp-4); transition: box-shadow .16s ease-out, border-color .16s ease-out, transform .16s ease-out;
}
.ui-card:hover { border-color: var(--accent-line); box-shadow: var(--shadow-2); }
[data-dir='bauhaus'] .ui-card { box-shadow: var(--shadow-1); border-color: var(--ink); }
[data-dir='bauhaus'] .ui-card:hover { transform: translate(-1px,-1px); box-shadow: var(--shadow-2); }
.ui-card .row { display: flex; gap: 12px; align-items: center; }
.ui-card .ava {
  width: 46px; height: 46px; border-radius: var(--radius); flex: none;
  display: grid; place-items: center; color: #fff; font-weight: 700; font-size: var(--fs-lg);
}
[data-dir='nocturne'] .ui-card .ava { border-radius: 50%; }
.ui-card .name { font-weight: 650; display: flex; align-items: center; gap: 6px; }
.ui-card .name a { color: inherit; text-decoration: none; }
.ui-card .name a:hover { color: var(--accent); }
.ui-card .stats { color: var(--muted); font-size: var(--fs-sm); margin-top: 2px; font-family: var(--font-mono); }
.ui-card .sig { margin: 11px 0 0; font-size: var(--fs-sm); color: var(--ink-2); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 34px; }
.ui-card .foot { margin-top: 12px; padding-top: 11px; border-top: 1px solid var(--line-soft); display: flex; align-items: center; gap: 14px; font-size: var(--fs-sm); color: var(--muted); }
[data-dir='bauhaus'] .ui-card .foot { border-top-width: 2px; border-top-color: var(--ink); }
.ui-toggle { display: inline-flex; align-items: center; gap: 7px; }
.ui-icon-btn {
  width: 30px; height: 30px; display: grid; place-items: center;
  border: 1px solid var(--line); background: var(--surface); border-radius: var(--radius);
  color: var(--ink-2); cursor: pointer; font-size: 15px;
}
.ui-icon-btn:hover { border-color: var(--accent); color: var(--accent); }

.ui-switch {
  width: 36px; height: 20px; border-radius: var(--radius-pill); background: var(--surface-3);
  border: 1px solid var(--line); position: relative; cursor: pointer; padding: 0; flex: none;
  transition: background .16s ease-out, border-color .16s ease-out;
}
.ui-switch i { position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: var(--radius-pill); background: var(--surface); box-shadow: var(--shadow-1); transition: transform .16s cubic-bezier(.16,1,.3,1); }
.ui-switch.on { background: var(--accent); border-color: var(--accent); }
.ui-switch.on i { transform: translateX(16px); }
.ui-switch.sm { width: 30px; height: 17px; }
.ui-switch.sm i { width: 11px; height: 11px; }
.ui-switch.sm.on i { transform: translateX(13px); }
[data-dir='nocturne'] .ui-switch.on { box-shadow: 0 0 12px color-mix(in oklab, var(--accent) 55%, transparent); }

/* 素材卡 */
.ui-card.asset { padding: 0; overflow: hidden; }
.ui-card.asset .thumb {
  aspect-ratio: 16 / 9; position: relative; display: flex; align-items: flex-start;
  justify-content: space-between; padding: 8px 10px; color: #fff; font-size: var(--fs-xs); font-weight: 600;
}
.ui-card.asset .thumb .dur { background: oklch(0.2 0 0 / .55); border-radius: var(--radius); padding: 1px 6px; font-family: var(--font-mono); }
.ui-card.asset .a-title { padding: 12px 14px 0; font-weight: 600; font-size: var(--fs-md); }
.ui-card.asset .a-meta { padding: 4px 14px 0; color: var(--muted); font-size: var(--fs-sm); }
.ui-card.asset .a-nums { padding: 8px 14px 14px; display: flex; gap: 12px; color: var(--muted); font-size: var(--fs-sm); font-family: var(--font-mono); }

/* 时间线 */
.ui-timeline { display: flex; flex-direction: column; }
.tl-item { display: flex; align-items: center; gap: 12px; padding: 11px 0; border-bottom: 1px solid var(--line-soft); }
.tl-item:last-child { border-bottom: 0; }
.tl-t { font-weight: 550; color: var(--ink); font-size: var(--fs-md); }
.tl-m { color: var(--muted); font-size: var(--fs-sm); margin-top: 2px; }

/* 表单 */
.ui-form { display: grid; grid-template-columns: 128px 1fr; gap: 14px var(--sp-4); align-items: center; padding: var(--sp-5); }
.ui-form label { font-size: var(--fs-md); color: var(--ink-2); }
.ui-input {
  width: 100%; padding: 8px 11px; font-size: var(--fs-md); font-family: inherit;
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--line); border-radius: var(--radius);
}
.ui-input:focus { outline: 2px solid var(--accent-line); outline-offset: 1px; border-color: var(--accent); }
.ui-help { color: var(--muted); font-size: var(--fs-sm); }

.ui-note { color: var(--muted); font-size: var(--fs-sm); line-height: 1.7; border-top: 1px solid var(--line-soft); padding-top: var(--sp-4); }
</style>
