<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>处理队列</h1>
      <span class="sub">下载 · 转录 · 视频理解 · 采集 · 评论抓取 的后台任务</span>
      <div class="grow" />
      <button class="ui-btn" :disabled="loading" @click="load">刷新</button>
      <button v-if="counts.error" class="ui-btn danger" @click="retryAll">全部重试</button>
    </div>

    <!-- 状态概览：一眼看清队列健康状况 -->
    <div class="pq-summary">
      <button
        v-for="s in summaryCards" :key="s.k"
        class="pq-stat" :class="[s.tone, { on: filter === s.k }]"
        @click="setFilter(s.k)"
      >
        <span class="pq-stat-n">{{ s.n }}</span>
        <span class="pq-stat-l">{{ s.label }}</span>
      </button>
      <div class="pq-auto">
        <span class="ui-dot ok" />每 5 秒自动刷新
        <template v-if="lastLoaded"> · {{ lastLoaded }}</template>
      </div>
    </div>

    <section class="ui-panel" v-loading="loading">
      <div v-if="!loading && !jobs.length" class="ui-empty">
        <div class="big">{{ filter ? '该状态下没有任务' : '队列是空的' }}</div>
        <p>{{ filter ? '换个筛选条件看看，或点上方「全部」。' : '发起采集、同步或加工后，任务会按顺序出现在这里。' }}</p>
      </div>

      <!-- 任务卡片：用人话说清「在加工什么、进行到哪、排了多久」 -->
      <ul v-else class="pq-list">
        <li v-for="(row, i) in jobs" :key="row.id" class="pq-item" :class="statusTone(row.status)">
          <!-- 左：序号 + 排队位置 -->
          <div class="pq-pos">
            <span class="pq-id">#{{ row.id }}</span>
            <span v-if="row.status === 'queued'" class="pq-rank">第 {{ i + 1 }} 位</span>
          </div>

          <!-- 中：在做什么 + 目标 + 时间线 -->
          <div class="pq-main">
            <div class="pq-title">
              <span class="pq-type">{{ typeText(row.job_type) }}</span>
              <span class="pq-target" :title="row.target_title || ''">
                {{ row.target_title || targetFallback(row) }}
              </span>
              <span class="ui-badge" :class="statusTone(row.status)">{{ statusText(row.status) }}</span>
            </div>

            <div class="pq-meta">
              <span class="pq-stage">
                <span class="pq-k">进度</span>{{ stageText(row) }}
              </span>
              <span><span class="pq-k">排队</span>{{ waited(row) }}</span>
              <span><span class="pq-k">用时</span>{{ elapsed(row) }}</span>
              <span><span class="pq-k">尝试</span>{{ row.attempts }}/{{ row.max_attempts }}</span>
              <span v-if="row.status === 'queued' && nextRun(row)">
                <span class="pq-k">计划</span>{{ nextRun(row) }}
              </span>
            </div>

            <!-- 失败原因：完整展示，不截断 -->
            <div v-if="row.error" class="pq-error">
              <span class="pq-err-k">失败原因</span>
              <p class="pq-err-v">{{ row.error }}</p>
            </div>
          </div>

          <!-- 右：操作 -->
          <div class="pq-acts">
            <button v-if="row.status === 'error'" class="ui-btn sm" @click="retry(row)">重试</button>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

const jobs = ref<any[]>([])
const loading = ref(false)
const filter = ref('')
const counts = ref({ queued: 0, running: 0, error: 0, done: 0 })
const lastLoaded = ref('')
let timer: number | undefined

const summaryCards = computed(() => [
  { k: '', label: '全部', n: counts.value.queued + counts.value.running + counts.value.error + counts.value.done, tone: '' },
  { k: 'queued', label: '排队中', n: counts.value.queued, tone: 'warn' },
  { k: 'running', label: '执行中', n: counts.value.running, tone: 'run' },
  { k: 'error', label: '失败', n: counts.value.error, tone: 'bad' },
  { k: 'done', label: '已完成', n: counts.value.done, tone: 'ok' },
])

function setFilter(v: string) {
  filter.value = v
  load()
}

const TYPE_TEXT: Record<string, string> = {
  download: '下载素材', transcribe: '语音转文字', analyze: 'AI 分析', understand_video: '视频理解',
  sync: '同步收藏', crawl: '关键词采集', fetch_comments: '抓取评论', breakdown: '爆款拆解',
}
function typeText(v: string) { return TYPE_TEXT[v] || v }

const STATUS_TEXT: Record<string, string> = {
  queued: '排队中', running: '执行中', done: '已完成', error: '失败',
}
function statusText(v: string) { return STATUS_TEXT[v] || v }
function statusTone(v: string) {
  return ({ queued: 'warn', running: 'run', done: 'ok', error: 'bad' } as Record<string, string>)[v] || ''
}

const PLATFORM_CN: Record<string, string> = {
  douyin: '抖音', bilibili: 'B站', kuaishou: '快手', zhihu: '知乎',
  xhs: '小红书', wechat_mp: '微信公众号',
}
function cnPlatform(v: string) { return PLATFORM_CN[v] || v || '' }

/** payload 里没有可读标题时的兜底文案：尽量说明「在加工什么」，而不是丢一个裸 id。 */
function targetFallback(row: any) {
  const p = row.payload || {}
  if (p.asset_id !== undefined) return `素材 #${p.asset_id}`
  if (p.content_id !== undefined) return `内容 #${p.content_id}`
  if (p.task_id !== undefined) return `采集任务 #${p.task_id}`
  if (p.subscription_id !== undefined) return `订阅 #${p.subscription_id}`
  // 采集/同步类任务通常没有具体素材，用类型 + 平台说明在做什么
  const hint = p.task_hint || p.subscription_hint || p.asset_hint || p.platform
  const plat = hint ? ` · ${cnPlatform(hint)}` : ''
  if (row.job_type === 'crawl') return `关键词采集${plat}`
  if (row.job_type === 'sync') return `同步收藏${plat}`
  if (row.job_type === 'download') return `下载队列${plat}`
  return typeText(row.job_type)
}

/** 进度：用状态 + payload 里的阶段信息解释，而不是只给一个词。 */
function stageText(row: any) {
  const p = row.payload || {}
  if (row.status === 'queued') return '等待 worker 领取'
  if (row.status === 'running') return p.stage || '处理中…'
  if (row.status === 'done') return '已完成'
  return `失败（第 ${row.attempts} 次尝试）`
}

function parseTs(v: string | null | undefined) {
  if (!v) return null
  return new Date(/Z$/.test(v) || v.includes('+') ? v : v + 'Z').getTime()
}
function human(ms: number | null) {
  if (ms === null || ms < 0) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分 ${s % 60} 秒`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时 ${m % 60} 分`
  return `${Math.floor(h / 24)} 天 ${h % 24} 小时`
}

/** 排队时长：创建 -> 开始（执行中/已完成）或 创建 -> 现在（仍在排队）。 */
function waited(row: any) {
  const created = parseTs(row.created_at)
  if (!created) return '—'
  const start = parseTs(row.locked_at)
    ?? (row.status === 'queued' ? Date.now() : (parseTs(row.updated_at) ?? Date.now()))
  return human(start - created)
}
/** 处理用时：优先 locked_at -> 现在/结束；没有 lock 记录时退化为 created -> updated。 */
function elapsed(row: any) {
  if (row.status === 'queued') return '—'
  const start = parseTs(row.locked_at) ?? parseTs(row.created_at)
  if (!start) return '—'
  const end = row.status === 'running' ? Date.now() : (parseTs(row.updated_at) ?? Date.now())
  if (!end) return '—'
  return human(end - start)
}
function nextRun(row: any) {
  const t = parseTs(row.run_after)
  if (!t || t <= Date.now() + 1000) return ''
  return new Date(t).toLocaleTimeString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  try {
    const q = filter.value ? `?status=${filter.value}` : ''
    // 列表按当前筛选取；计数始终基于全量，避免筛选后徽标互相矛盾
    const [listResp, allResp] = await Promise.all([
      api.get(`/api/pipeline/jobs${q}`),
      filter.value ? api.get('/api/pipeline/jobs') : null,
    ])
    jobs.value = listResp.data
    const all = (allResp?.data ?? listResp.data) as any[]
    counts.value = {
      queued: all.filter((j) => j.status === 'queued').length,
      running: all.filter((j) => j.status === 'running').length,
      error: all.filter((j) => j.status === 'error').length,
      done: all.filter((j) => j.status === 'done').length,
    }
    lastLoaded.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } finally {
    loading.value = false
  }
}

async function retry(row: any) {
  await api.post(`/api/pipeline/jobs/${row.id}/retry`)
  ElMessage.success('已重新排队')
  await load()
}

async function retryAll() {
  await api.post('/api/pipeline/jobs/retry-failed')
  ElMessage.success('已重试全部失败任务')
  await load()
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 5000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<style scoped>
/* 状态概览 */
.pq-summary { display: flex; gap: var(--sp-2); align-items: stretch; flex-wrap: wrap; }
.pq-stat {
  appearance: none; cursor: pointer; font-family: inherit; text-align: left;
  display: flex; flex-direction: column; gap: 2px; min-width: 92px;
  padding: 10px 14px;
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--ink); box-shadow: var(--shadow-1);
  transition: transform .12s ease-out, box-shadow .12s ease-out;
}
.pq-stat:hover { transform: translate(-1px, -1px); }
.pq-stat.on { box-shadow: var(--shadow-2); border-width: 2px; }
.pq-stat-n { font-size: 24px; font-weight: 750; line-height: 1.1; font-variant-numeric: tabular-nums; }
.pq-stat-l { font-size: var(--fs-sm); color: var(--muted); }
.pq-stat.warn .pq-stat-n { color: var(--warn-strong, var(--warn)); }
.pq-stat.run .pq-stat-n { color: var(--accent-2); }
.pq-stat.bad .pq-stat-n { color: var(--danger); }
.pq-stat.ok .pq-stat-n { color: var(--ok); }
.pq-stat.on .pq-stat-l { color: var(--ink); }
.pq-auto { margin-left: auto; align-self: center; display: flex; align-items: center; gap: 7px; font-size: var(--fs-sm); color: var(--muted); }

/* 任务列表 */
.pq-list { list-style: none; margin: 0; padding: 0; }
.pq-item {
  display: grid; grid-template-columns: 84px 1fr auto; gap: 14px;
  padding: 12px var(--sp-4);
  border-bottom: 1px solid var(--line-soft);
}
.pq-item:last-child { border-bottom: 0; }
.pq-item.bad { background: color-mix(in oklab, var(--danger) 5%, var(--surface)); }
.pq-item.run { background: color-mix(in oklab, var(--accent-2) 4%, var(--surface)); }

.pq-pos { display: flex; flex-direction: column; gap: 3px; }
.pq-id { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--muted); }
.pq-rank { font-size: var(--fs-xs); color: var(--accent-2); font-weight: 650; }

.pq-main { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.pq-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.pq-type {
  flex: none; font-size: var(--fs-xs); font-weight: 700; padding: 1px 7px;
  border: 1px solid var(--ink); background: var(--surface-2); color: var(--ink);
}
.pq-target {
  font-weight: 600; font-size: var(--fs-md); color: var(--ink);
  min-width: 0; word-break: break-word;
}

.pq-meta { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: var(--fs-sm); color: var(--ink-2); }
.pq-k { color: var(--muted); margin-right: 5px; }
.pq-stage { color: var(--ink); }

/* 失败原因：换行完整显示，不截断 */
.pq-error { display: flex; gap: 8px; align-items: flex-start; margin-top: 2px; }
.pq-err-k { flex: none; font-size: var(--fs-xs); color: var(--danger); font-weight: 650; padding-top: 1px; }
.pq-err-v {
  margin: 0; color: var(--danger); font-size: var(--fs-sm); line-height: 1.5;
  white-space: pre-wrap; word-break: break-word;
}
/* 错误信息完整展示，不做任何截断（用户要求：详细信息必须展示完全） */

.pq-acts { display: flex; align-items: center; }

@media (max-width: 860px) {
  .pq-item { grid-template-columns: 1fr; }
  .pq-pos { flex-direction: row; gap: 10px; }
}
</style>
