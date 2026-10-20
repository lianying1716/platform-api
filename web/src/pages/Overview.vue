<template>
  <div class="ov" v-loading="loading">
    <!-- 页头：标题降级，工具条归位到右侧 -->
    <div class="ov-top">
      <div class="ov-title">
        <h1>总览</h1>
        <span class="sub">你的个人素材库</span>
      </div>
      <div class="grow" />
      <div class="ui-seg ov-seg">
        <button v-for="r in trendRanges" :key="r.k" :class="{ on: trendRange === r.k }" @click="trendRange = r.k">
          {{ r.label }}
        </button>
      </div>
      <el-select v-model="accountId" placeholder="全部账号" clearable size="small" style="width: 170px" @change="load">
        <el-option
          v-for="a in accounts"
          :key="a.id"
          :label="a.display_name || `${a.platform_label || a.platform} #${a.id}`"
          :value="a.id"
        />
      </el-select>
      <button class="ui-btn sm" :disabled="loading" @click="load">刷新</button>
    </div>

    <!-- 头条：一句话说清「库里有什么 + 有什么要处理」，右上是待办动作 -->
    <section class="ov-hero">
      <div class="ov-hero-left">
        <p class="ov-hero-line">
          素材库里已有 <b>{{ num(content.total) }}</b> 条
        </p>
        <p class="ov-hero-sub">
          视频总时长 {{ durText }} · 占用 {{ bytesText }} · 覆盖 {{ num(accountsStat.total) }} 个账号
        </p>
      </div>
      <div class="ov-hero-todo" :class="{ hot: todoCount > 0 }">
        <template v-if="todoCount > 0">
          <span class="ov-todo-n">{{ num(todoCount) }}</span>
          <span class="ov-todo-l">项待办</span>
          <button class="ui-btn sm" @click="go(todoTarget)">{{ todoAction }}</button>
        </template>
        <template v-else>
          <span class="ov-todo-ok"><span class="ui-dot ok" />队列已清空</span>
          <span class="ov-todo-l">没有待办</span>
        </template>
      </div>
    </section>

    <!-- 主指标：只留 4 个真正要紧的，字号拉开层级 -->
    <div class="ov-main">
      <button class="ov-kpi tone-ink" @click="go('/assets')">
        <span class="ov-kpi-k">素材总量</span>
        <span class="ov-kpi-v">{{ num(content.total) }}</span>
        <span class="ov-kpi-d">{{ num(content.video) }} 视频 · {{ num(content.article) }} 文章</span>
      </button>
      <button class="ov-kpi tone-warn" :class="{ warn: processing.pending + processing.failed > 0 }" @click="go('/pipeline')">
        <span class="ov-kpi-k">待加工</span>
        <span class="ov-kpi-v">{{ num(processing.pending) }}</span>
        <span class="ov-kpi-d">
          <template v-if="processing.failed">失败 {{ num(processing.failed) }} 条，需重试</template>
          <template v-else-if="processing.pending">等待队列处理</template>
          <template v-else>队列顺畅</template>
        </span>
      </button>
      <button class="ov-kpi tone-ok" @click="go('/assets')">
        <span class="ov-kpi-k">已判类</span>
        <span class="ov-kpi-v">{{ classifiedPct }}</span>
        <span class="ov-kpi-d">{{ num(categorizedCount) }} 已分类 · {{ num(uncategorizedCount) }} 待判</span>
      </button>
      <button class="ov-kpi tone-bad" :class="{ bad: accountsStat.abnormal > 0 }" @click="go('/accounts')">
        <span class="ov-kpi-k">账号在线</span>
        <span class="ov-kpi-v">{{ num(accountsStat.total - accountsStat.abnormal) }}<em>/{{ num(accountsStat.total) }}</em></span>
        <span class="ov-kpi-d">
          <template v-if="accountsStat.abnormal">{{ accountsStat.abnormal }} 个异常，去处理</template>
          <template v-else>全部正常</template>
        </span>
      </button>
    </div>

    <!-- 次级信息：一行紧凑条，不与主指标抢视觉 -->
    <div class="ov-sub">
      <span><b>{{ num(todayCrawled) }}</b> 今日入库</span>
      <span><b>{{ num(processing.understood) }}</b> 已视频理解</span>
      <span><b>{{ num(storageRoots.length) }}</b> 存储根</span>
      <span class="ov-sub-root" :title="storageRootText">{{ storageRootText }}</span>
    </div>

    <div class="ui-cols">
      <section class="ui-panel">
        <div class="ui-panel-head">
          <h2>新增趋势</h2>
          <span class="hint">近 14 天入库条数</span>
        </div>
        <div class="ui-panel-body ui-chart tall" v-if="trend.length"><EChart :build="trendChart" /></div>
        <div class="ui-panel-body" v-else><p class="ui-empty">暂无数据，跑一次采集或同步后这里会出现趋势。</p></div>
      </section>
      <section class="ui-panel">
        <div class="ui-panel-head">
          <h2>平台分布</h2>
          <span class="hint">视频 / 文章构成</span>
        </div>
        <div class="ui-panel-body ui-chart tall" v-if="platforms.length"><EChart :build="platformChart" /></div>
        <div class="ui-panel-body" v-else><p class="ui-empty">还没有素材。</p></div>
      </section>
    </div>

    <section class="ui-panel" v-if="platforms.length">
      <div class="ui-panel-head"><h2>各平台明细</h2><span class="hint">点击行进入素材库</span></div>
      <div class="ui-table-scroll">
        <table class="ui-table">
          <thead>
            <tr>
              <th>平台</th>
              <th style="text-align: right">素材</th>
              <th style="text-align: right">视频</th>
              <th style="text-align: right">文章</th>
              <th style="text-align: right">已转录</th>
              <th style="text-align: right">已判类</th>
              <th style="text-align: right">占用</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in platforms"
              :key="p.platform"
              @click="go(`/assets?platform=${p.platform}${accountId ? `&account_id=${accountId}` : ''}`)"
            >
              <td><PlatformMark :platform="p.platform" size="sm" /></td>
              <td class="num">{{ num(p.total) }}</td>
              <td class="num">{{ num(p.video) }}</td>
              <td class="num">{{ num(p.article) }}</td>
              <td class="num">{{ num(p.transcribed) }}</td>
              <td class="num">{{ num(p.categorized) }}</td>
              <td class="num">{{ bytes(storageByPlatform[p.platform] || 0) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="ui-panel" v-if="categories.length">
      <div class="ui-panel-head">
        <h2>内容分类</h2>
        <span class="hint">来自视频理解的判类结果，未判类的可去素材库补跑</span>
      </div>
      <div class="cat-grid">
        <button
          v-for="c in categories"
          :key="c.key || 'none'"
          class="cat"
          :class="{ none: !c.key }"
          @click="go(c.key ? `/assets?category=${c.key}` : '/assets')"
        >
          <span class="cat-label">{{ c.label }}</span>
          <span class="cat-count">{{ num(c.count) }}</span>
        </button>
      </div>
    </section>

    <div class="ui-cols">
      <section class="ui-panel">
        <div class="ui-panel-head">
          <h2>热门 Top</h2>
          <span class="hint">按最新快照播放量</span>
        </div>
        <div class="ui-table-scroll" v-if="hot.length">
          <table class="ui-table">
            <thead>
              <tr><th>#</th><th>标题</th><th>平台</th><th style="text-align: right">播放</th></tr>
            </thead>
            <tbody>
              <tr v-for="(h, i) in hot" :key="i">
                <td class="num">{{ i + 1 }}</td>
                <td class="strong ellip">{{ h.title || '（无标题）' }}</td>
                <td>{{ platformLabel(h.platform) }}</td>
                <td class="num">{{ compact(h.play) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="ui-panel-body" v-else><p class="ui-empty">暂无带指标的数据。</p></div>
      </section>

      <section class="ui-panel">
        <div class="ui-panel-head">
          <h2>对标账号</h2>
          <span class="hint">点「拆解」看内容策略报告</span>
        </div>
        <div class="ui-table-scroll" v-if="creators.length">
          <table class="ui-table">
            <thead>
              <tr><th>账号</th><th>平台</th><th style="text-align: right">内容数</th><th style="text-align: right">操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in creators" :key="c.author_id + c.platform">
                <td class="strong">{{ c.author_name || c.author_id }}</td>
                <td><PlatformMark :platform="c.platform" size="sm" :show-label="false" /></td>
                <td class="num">{{ num(c.content_count) }}</td>
                <td style="text-align: right">
                  <button class="ui-btn ghost sm" @click="openReport(c)">拆解</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="ui-panel-body" v-else><p class="ui-empty">还没有作者维度的数据。</p></div>
      </section>
    </div>

    <div v-if="reportOpen" class="ui-modal" @click.self="reportOpen = false">
      <div class="ui-modal-box">
        <div class="ui-panel-head">
          <h2>{{ report?.author_name || report?.author_id }} · 对标账号拆解</h2>
          <div class="grow" />
          <button class="ui-btn ghost sm" @click="reportOpen = false">关闭</button>
        </div>
        <div class="ui-modal-body" v-loading="reportLoading">
          <template v-if="report">
            <p class="sec">账号概览</p>
            <div class="rep-metrics">
              <div><span>内容数</span><b>{{ num(report.overview.content_count) }}</b></div>
              <div><span>平均播放</span><b>{{ compact(report.overview.avg_play) }}</b></div>
              <div><span>中位播放</span><b>{{ compact(report.overview.median_play) }}</b></div>
              <div><span>平均点赞</span><b>{{ compact(report.overview.avg_like) }}</b></div>
              <div><span>平均评论</span><b>{{ compact(report.overview.avg_comment) }}</b></div>
              <div><span>平均互动率</span><b>{{ pct(report.overview.avg_engagement_rate) }}</b></div>
            </div>

            <p class="sec">发布节奏（按小时）</p>
            <div class="rep-bars">
              <div v-for="h in report.cadence.by_hour" :key="h.hour" class="rep-bar">
                <span class="bl">{{ h.hour }}时</span>
                <span class="track"><i :style="{ width: barPct(h.count, maxHour) }" /></span>
                <span class="bv">{{ h.count }}</span>
              </div>
            </div>

            <p class="sec">Top 作品</p>
            <div class="ui-table-scroll">
              <table class="ui-table">
                <thead><tr><th>标题</th><th>类型</th><th style="text-align: right">播放</th><th style="text-align: right">点赞</th></tr></thead>
                <tbody>
                  <tr v-for="(t, i) in report.top" :key="i">
                    <td class="strong ellip">
                      <span v-if="t.viral" class="ui-badge bad">爆</span>
                      <a v-if="safeExternalUrl(t.url)" :href="safeExternalUrl(t.url)" target="_blank" rel="noopener noreferrer">{{ t.title || '（无标题）' }}</a>
                      <span v-else>{{ t.title || '（无标题）' }}</span>
                    </td>
                    <td>{{ t.category || '—' }}</td>
                    <td class="num">{{ compact(t.play) }}</td>
                    <td class="num">{{ compact(t.like) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <p class="sec">对标基准</p>
            <div class="rep-metrics">
              <div><span>本号中位播放</span><b>{{ compact(report.benchmark.author_median_play) }}</b></div>
              <div><span>平台中位播放</span><b>{{ compact(report.benchmark.platform_median_play) }}</b></div>
              <div><span>本号 / 平台</span><b>{{ mult(report.benchmark.author_vs_platform) }}</b></div>
              <div><span>平台样本</span><b>{{ report.benchmark.platform_snapshot_count }} / {{ report.benchmark.platform_content_count }}</b></div>
            </div>

            <template v-if="report.gaps?.length">
              <p class="sec">内容缺口</p>
              <div class="rep-gaps">
                <span v-for="g in report.gaps" :key="g.category" class="ui-badge warn">
                  {{ g.category }}（平台中位 {{ compact(g.platform_median_play) }}）
                </span>
              </div>
            </template>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 显式组件名：Layout 的 keep-alive include 按组件名匹配，切页返回时保留本页数据。
defineOptions({ name: 'OverviewPage' })
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import PlatformMark from '../components/PlatformMark.vue'
import { api } from '../api'
import { safeExternalUrl } from '../utils/safeUrl'
import EChart from '../components/charts/EChart.vue'

const router = useRouter()
const loading = ref(false)
const accountId = ref<number | null>(null)
const accounts = ref<Array<{ id: number; platform: string; platform_label?: string; display_name: string }>>([])
const content = ref({ total: 0, video: 0, article: 0, duration_sec: 0 })
const platforms = ref<any[]>([])
const categories = ref<any[]>([])
const processing = ref({ transcribed: 0, understood: 0, pending: 0, failed: 0 })
const accountsStat = ref({ total: 0, abnormal: 0 })
const storageByPlatform = ref<Record<string, number>>({})
const trend = ref<{ date: string; added: number }[]>([])
const hot = ref<any[]>([])
const creators = ref<any[]>([])
const todayCrawled = ref(0)
const weekCrawled = ref(0)
const storageUsed = ref(0)
const storageRoots = ref<any[]>([])
const trendRange = ref<'14' | '7'>('14')

const trendRanges = [
  { k: '14', label: '近 14 天' },
  { k: '7', label: '近 7 天' },
]

const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音',
  bilibili: 'B站',
  kuaishou: '快手',
  zhihu: '知乎',
  xhs: '小红书',
  wechat_mp: '微信公众号',
}
function platformLabel(p: string) {
  return PLATFORM_LABELS[p] || p || '未知'
}

function num(v: any) {
  return Number(v || 0).toLocaleString('zh-CN')
}
function compact(v: any) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v || 0)
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`
  return num(n)
}
function pct(v: any, d = 1) {
  if (v === null || v === undefined) return '—'
  return `${(Number(v || 0) * 100).toFixed(d)}%`
}
function mult(v: any) {
  return v ? `${Number(v).toFixed(2)}×` : '—'
}
function bytes(v: any) {
  const n = Number(v || 0)
  if (n <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let x = n
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024
    i += 1
  }
  return `${x.toFixed(i >= 3 ? 2 : 0)} ${units[i]}`
}
function dur(sec: number) {
  const s = Math.max(0, Math.round(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return `${h} 小时 ${m} 分`
  return `${m} 分 ${s % 60} 秒`
}

const categorizedCount = computed(() => {
  const known = categories.value.filter((c) => c.key)
  return known.reduce((sum, c) => sum + Number(c.count || 0), 0)
})
const uncategorizedCount = computed(
  () => Number(categories.value.find((c) => !c.key)?.count || 0),
)
const classifiedPct = computed(() => {
  const total = categorizedCount.value + uncategorizedCount.value
  if (!total) return '—'
  return `${Math.round((categorizedCount.value / total) * 100)}%`
})
// 待办：加工失败的优先，其次队列积压，最后账号异常。决定 hero 右上角显示什么。
// pending 只含 queued/running；failed 是独立终态，必须单独相加。
const todoCount = computed(
  () => (processing.value.pending || 0) + (processing.value.failed || 0) + (accountsStat.value.abnormal || 0),
)
const todoTarget = computed(() => {
  if (processing.value.failed || processing.value.pending) return '/pipeline'
  if (accountsStat.value.abnormal) return '/accounts'
  return '/pipeline'
})
const todoAction = computed(() => (todoTarget.value === '/pipeline' ? '去处理' : '去查看'))

const bytesText = computed(() => bytes(storageUsed.value))
const durText = computed(() => dur(content.value.duration_sec || 0))
const avgDur = computed(() => {
  const v = content.value.video
  return v ? dur((content.value.duration_sec || 0) / v) : '—'
})
const storageRootText = computed(() => {
  const r = storageRoots.value.find((x) => x.is_default) || storageRoots.value[0]
  if (!r) return '未配置存储根'
  return `${r.name}${r.available ? '' : '（不可用）'}`
})
const maxHour = computed(() =>
  Math.max(1, ...((report.value?.cadence?.by_hour || []) as any[]).map((h) => h.count)),
)
function barPct(v: number, max: number) {
  return `${max ? Math.round((v / max) * 100) : 0}%`
}

const trendData = computed(() =>
  trendRange.value === '7' ? trend.value.slice(-7) : trend.value,
)
const trendChart = (t: Record<string, string>) => ({
  tooltip: {
    trigger: 'axis',
    backgroundColor: t['--surface'],
    borderColor: t['--ink'],
    textStyle: { color: t['--ink'] },
  },
  grid: { left: 44, right: 18, top: 18, bottom: 28 },
  xAxis: {
    type: 'category',
    data: trendData.value.map((d) => d.date),
    axisLabel: { color: t['--muted'] },
    axisLine: { lineStyle: { color: t['--line'] } },
    axisTick: { show: false },
  },
  yAxis: {
    type: 'value',
    splitLine: { lineStyle: { color: t['--chart-grid'] } },
    axisLabel: { color: t['--muted'] },
  },
  series: [
    {
      type: 'bar',
      name: '新增',
      data: trendData.value.map((d) => d.added),
      itemStyle: { color: t['--accent'], borderColor: t['--ink'], borderWidth: 1 },
      barMaxWidth: 26,
    },
    {
      type: 'line',
      name: '趋势',
      data: trendData.value.map((d) => d.added),
      smooth: 0.3,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { width: 2, color: t['--accent-2'] },
      itemStyle: { color: t['--accent-2'], borderColor: t['--ink'], borderWidth: 1 },
    },
  ],
  color: [t['--accent'], t['--accent-2']],
})

const platformChart = (t: Record<string, string>) => ({
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    backgroundColor: t['--surface'],
    borderColor: t['--ink'],
    textStyle: { color: t['--ink'] },
  },
  legend: {
    bottom: 0,
    icon: 'rect',
    itemWidth: 11,
    itemHeight: 11,
    textStyle: { color: t['--ink-2'] },
  },
  grid: { left: 44, right: 18, top: 18, bottom: 46 },
  xAxis: {
    type: 'category',
    data: platforms.value.map((p) => platformLabel(p.platform)),
    axisLabel: { color: t['--muted'] },
    axisLine: { lineStyle: { color: t['--line'] } },
    axisTick: { show: false },
  },
  yAxis: {
    type: 'value',
    splitLine: { lineStyle: { color: t['--chart-grid'] } },
    axisLabel: { color: t['--muted'] },
  },
  series: [
    {
      name: '视频',
      type: 'bar',
      stack: 'x',
      data: platforms.value.map((p) => p.video),
      itemStyle: { color: t['--accent'], borderColor: t['--ink'], borderWidth: 1 },
      barMaxWidth: 34,
    },
    {
      name: '文章',
      type: 'bar',
      stack: 'x',
      data: platforms.value.map((p) => p.article),
      itemStyle: { color: t['--accent-2'], borderColor: t['--ink'], borderWidth: 1 },
      barMaxWidth: 34,
    },
  ],
  color: [t['--accent'], t['--accent-2']],
})

function go(to: string) {
  router.push(to)
}

const reportOpen = ref(false)
const reportLoading = ref(false)
const report = ref<any>(null)
async function openReport(row: any) {
  reportOpen.value = true
  reportLoading.value = true
  report.value = null
  try {
    const { data } = await api.get(
      `/api/analytics/creator/${encodeURIComponent(row.author_id)}/report`,
      { params: { platform: row.platform } },
    )
    report.value = data
  } catch {
    reportOpen.value = false
  } finally {
    reportLoading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const [{ data }, creatorsResp] = await Promise.all([
      api.get('/api/dashboard/summary', { params: accountId.value ? { account_id: accountId.value } : {} }),
      api.get('/api/analytics/creators', {
        params: { limit: 8, ...(accountId.value ? { account_id: accountId.value } : {}) },
      }).catch(() => ({ data: [] })),
    ])
    content.value = data.content || content.value
    platforms.value = data.platforms || []
    categories.value = data.categories || []
    processing.value = data.processing || processing.value
    trend.value = data.trend || []
    hot.value = data.hot || []
    todayCrawled.value = data.today_crawled || 0
    weekCrawled.value = data.week_crawled || 0
    storageUsed.value = data.storage?.used_bytes || 0
    storageByPlatform.value = data.storage?.by_platform || {}
    storageRoots.value = data.storage?.roots || []
    accountsStat.value = {
      total: data.accounts?.total || 0,
      abnormal: data.accounts?.abnormal || 0,
    }
    creators.value = creatorsResp.data || []
  } catch {
    ElMessage.error('加载总览失败')
  } finally {
    loading.value = false
  }
}

async function loadAccounts() {
  const { data } = await api.get('/api/accounts')
  accounts.value = data
}

onMounted(async () => {
  await loadAccounts()
  await load()
})
</script>

<style scoped>
.ov { display: flex; flex-direction: column; gap: var(--sp-5); }

/* 页头：标题不再抢戏，工具条靠右 */
.ov-top { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.ov-title { display: flex; align-items: baseline; gap: 10px; }
.ov-title h1 { margin: 0; font-size: 22px; letter-spacing: -0.02em; }
.ov-title .sub { color: var(--muted); font-size: var(--fs-sm); }
.ov-top .grow { margin-left: auto; }

/* 头条：一句话结论 + 待办入口（首屏第一视线） */
.ov-hero {
  display: flex; align-items: center; gap: var(--sp-5); flex-wrap: wrap;
  padding: var(--sp-5) var(--sp-5);
  background: var(--surface);
  border: 2px solid var(--ink);
  box-shadow: var(--shadow-2);
  border-left: 0;
}
.ov-hero > * { position: relative; }
.ov-hero-left { flex: 1; min-width: 260px; }
.ov-hero-line { margin: 0; font-size: 26px; line-height: 1.25; color: var(--ink-2); font-weight: 500; }
.ov-hero-line b { font-size: 40px; font-weight: 800; letter-spacing: -0.03em; color: var(--ink); font-variant-numeric: tabular-nums; }
.ov-sep { margin: 0 6px; color: var(--line-soft); }
.ov-soft { color: var(--muted); font-size: 15px; }
.ov-hero-sub { margin: 8px 0 0; color: var(--muted); font-size: var(--fs-sm); }

.ov-hero-todo {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 12px 16px; border: 1px solid var(--line-soft); background: var(--surface-2);
}
.ov-hero-todo.hot { border: 2px solid var(--accent); background: var(--surface); }
.ov-hero-todo.hot .ov-todo-n { color: var(--accent); }
.ov-todo-n { font-size: 30px; font-weight: 800; line-height: 1; color: var(--accent); font-variant-numeric: tabular-nums; }
.ov-todo-l { font-size: var(--fs-sm); color: var(--ink-2); }
.ov-todo-ok { display: inline-flex; align-items: center; gap: 7px; font-weight: 650; }

/* 主指标：4 个，字号明显大于次级信息 */
.ov-main { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--sp-3); }
.ov-kpi {
  appearance: none; cursor: pointer; font-family: inherit; text-align: left;
  display: flex; flex-direction: column; gap: 4px;
  padding: var(--sp-4) var(--sp-4) calc(var(--sp-4) - 2px);
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--ink); box-shadow: var(--shadow-1);
  border-top: 4px solid var(--ink);
  transition: transform .12s ease-out, box-shadow .12s ease-out;
}
.ov-kpi:hover { transform: translate(-1px, -1px); box-shadow: var(--shadow-2); }
.ov-kpi { border-top: 5px solid var(--ink); }
.ov-kpi.tone-ink { border-top-color: var(--accent); }
.ov-kpi.tone-warn { border-top-color: var(--warn); }
.ov-kpi.tone-ok { border-top-color: var(--ok); }
.ov-kpi.tone-bad { border-top-color: var(--danger); }
.ov-kpi-k { font-size: var(--fs-sm); color: var(--muted); }
.ov-kpi-v { font-size: 36px; font-weight: 800; line-height: 1.05; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
.ov-kpi-v em { font-style: normal; font-size: 18px; color: var(--muted); font-weight: 650; }
.ov-kpi-d { font-size: var(--fs-xs); color: var(--muted); margin-top: 2px; }
.ov-kpi.warn .ov-kpi-v { color: var(--warn-strong, var(--warn)); }
.ov-kpi.bad .ov-kpi-v { color: var(--danger); }

/* 次级信息：一行紧凑条 */
.ov-sub {
  display: flex; align-items: center; gap: var(--sp-5); flex-wrap: wrap;
  padding: 9px var(--sp-4);
  border: 1px dashed var(--line-soft);
  font-size: var(--fs-sm); color: var(--muted);
}
.ov-sub b { color: var(--ink); font-variant-numeric: tabular-nums; }
.ov-sub-root { margin-left: auto; max-width: 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.ui-cols { display: grid; grid-template-columns: 1.15fr 1fr; gap: var(--sp-4); }
@media (max-width: 1180px) {
  .ov-main { grid-template-columns: repeat(2, 1fr); }
  .ui-cols { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .ov-main { grid-template-columns: 1fr; }
}

.cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(148px, 1fr)); gap: 8px; padding: var(--sp-4); }
.cat {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 9px 12px; font-family: inherit; font-size: var(--fs-md); cursor: pointer;
  background: var(--surface); color: var(--ink-2);
  border: 1px solid var(--ink); box-shadow: 2px 2px 0 var(--ink);
  transition: transform .12s ease-out, box-shadow .12s ease-out;
}
.cat:hover { transform: translate(-1px, -1px); box-shadow: 3px 3px 0 var(--ink); color: var(--ink); }
.cat:active { transform: translate(1px, 1px); box-shadow: 1px 1px 0 var(--ink); }
.cat.none { border-style: dashed; border-color: var(--muted); box-shadow: none; color: var(--muted); }
.cat-count { font-weight: 700; font-variant-numeric: tabular-nums; }

.ellip { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.ui-modal {
  position: fixed; inset: 0; z-index: 200;
  background: color-mix(in oklab, var(--ink) 42%, transparent);
  display: grid; place-items: center; padding: var(--sp-5);
}
.ui-modal-box {
  width: min(880px, 100%); max-height: 88vh; display: flex; flex-direction: column;
  background: var(--surface); border: 2px solid var(--ink); box-shadow: var(--shadow-2);
}
.ui-modal-body { padding: var(--sp-5); overflow: auto; }
.sec { margin: 18px 0 8px; font-weight: 700; font-size: var(--fs-md); border-left: 4px solid var(--accent); padding-left: 9px; }
.sec:first-child { margin-top: 0; }
.rep-metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.rep-metrics > div { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; border: 1px solid var(--line-soft); }
.rep-metrics span { font-size: var(--fs-xs); color: var(--muted); }
.rep-metrics b { font-variant-numeric: tabular-nums; }
.rep-bars { display: flex; flex-direction: column; gap: 4px; }
.rep-bar { display: flex; align-items: center; gap: 8px; font-size: var(--fs-sm); }
.rep-bar .bl { width: 42px; color: var(--muted); }
.rep-bar .track { flex: 1; height: 10px; border: 1px solid var(--ink); background: var(--surface-2); }
.rep-bar .track i { display: block; height: 100%; background: var(--accent); }
.rep-bar .bv { width: 26px; text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.rep-gaps { display: flex; flex-wrap: wrap; gap: 6px; }
</style>
