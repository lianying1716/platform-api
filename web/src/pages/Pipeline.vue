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

    <div v-if="aiSchedule.enabled" class="pq-ai" :class="aiSchedule.is_open ? 'open' : 'closed'">
      <span class="ui-badge" :class="aiSchedule.is_open ? 'ok' : 'warn'">
        AI {{ aiSchedule.is_open ? '运行时段开放' : '运行时段已关闭' }}
      </span>
      <span v-if="!aiSchedule.is_open && aiSchedule.next_open_at" class="pq-ai-next">
        AI 任务暂缓，{{ aiSchedule.next_open_local || fmtAiTime(aiSchedule.next_open_at) }} 自动继续
      </span>
      <span v-else-if="!aiSchedule.is_open" class="pq-ai-next">AI 任务暂缓，等待下一个运行时段</span>
      <span v-else class="pq-ai-next">AI 速览、视频理解、爆款拆解会在此窗口内开始</span>
      <router-link class="pq-ai-link" to="/system">调整时段</router-link>
    </div>

    <section v-if="alerts.length" class="pq-alerts" aria-label="运行告警">
      <div v-for="alert in alerts" :key="alert.id" class="pq-alert" :class="alert.severity">
        <div class="pq-alert-main">
          <b>{{ alert.title }}</b>
          <span v-if="alert.occurrences > 1" class="pq-alert-count">×{{ alert.occurrences }}</span>
          <p>{{ alert.message }}</p>
          <span class="pq-alert-time">{{ fmtLogTime(alert.last_seen_at) }} · 任务 #{{ alert.job_id }}</span>
        </div>
        <div class="pq-alert-acts">
          <button v-if="alert.status === 'active'" class="ui-btn sm" @click="ackAlert(alert)">确认</button>
          <button class="ui-btn sm" @click="resolveAlert(alert)">关闭</button>
        </div>
      </div>
    </section>

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
              <span v-if="row.ai_schedule_held" class="pq-held">
                等待 {{ row.ai_next_open_local || fmtAiTime(row.ai_next_open_at) || 'AI 时段' }}
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
            <button class="ui-btn sm" @click="toggleLogs(row)">{{ openLogs === row.id ? '收起日志' : '日志' }}</button>
            <button v-if="row.status === 'error'" class="ui-btn sm" @click="retry(row)">重试</button>
          </div>
          <div v-if="openLogs === row.id" class="pq-logs">
            <div v-if="logsLoading" class="pq-log-empty">读取日志…</div>
            <div v-else-if="!logs.length" class="pq-log-empty">这条任务还没有结构化日志</div>
            <ol v-else class="pq-log-list">
              <li v-for="log in logs" :key="log.id" class="pq-log" :class="log.level">
                <span class="pq-log-time">{{ fmtLogTime(log.created_at) }}</span>
                <span class="pq-log-level">{{ log.level }}</span>
                <span class="pq-log-msg">{{ log.message }}</span>
                <span v-if="log.actor" class="pq-log-actor">{{ log.actor }}</span>
              </li>
            </ol>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import { parseUtc } from '../utils/time'

const jobs = ref<any[]>([])
const loading = ref(false)
const filter = ref('')
const counts = ref({ queued: 0, running: 0, error: 0, done: 0, total: 0 })
const lastLoaded = ref('')
const aiSchedule = ref<any>({ enabled: false, is_open: true, next_open_at: null })
let timer: number | undefined
let refreshOnEvent: (() => void) | undefined
const openLogs = ref<number | null>(null)
const logs = ref<any[]>([])
const logsLoading = ref(false)
const alerts = ref<any[]>([])

function alertToOut(row: any) {
  return {
    id: row.id,
    title: row.title,
    message: row.message,
    severity: row.severity === 'error' ? 'error' : 'warning',
    status: row.status,
    occurrences: row.occurrences,
    job_id: row.job_id,
    last_seen_at: row.last_seen_at,
  }
}

async function loadAlerts() {
  const { data } = await api.get('/api/pipeline/alerts')
  alerts.value = (data.alerts || []).map(alertToOut)
}

async function ackAlert(row: any) {
  await api.post(`/api/pipeline/alerts/${row.id}/acknowledge`)
  await loadAlerts()
}

async function resolveAlert(row: any) {
  await api.post(`/api/pipeline/alerts/${row.id}/resolve`)
  await loadAlerts()
}

function fmtLogTime(value: string | null) {
  const t = parseTs(value)
  return t ? new Date(t).toLocaleTimeString('zh-CN', { hour12: false }) : '—'
}

async function toggleLogs(row: any) {
  if (openLogs.value === row.id) {
    openLogs.value = null
    logs.value = []
    return
  }
  openLogs.value = row.id
  await loadLogs(row.id)
}

async function loadLogs(jobId: number) {
  logsLoading.value = true
  try {
    const { data } = await api.get(`/api/pipeline/jobs/${jobId}/logs`)
    logs.value = data.logs || []
  } finally {
    logsLoading.value = false
  }
}

const summaryCards = computed(() => [
  { k: '', label: '全部', n: counts.value.total, tone: '' },
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
  if (row.status === 'queued') {
    return row.ai_schedule_held ? '等待 AI 运行时段' : '等待 worker 领取'
  }
  if (row.status === 'running') return p.stage || '处理中…'
  if (row.status === 'done') return '已完成'
  return `失败（第 ${row.attempts} 次尝试）`
}

function parseTs(v: string | null | undefined) {
  if (!v) return null
  return parseUtc(v)?.getTime() ?? null
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

function fmtAiTime(value: string | null) {
  if (!value) return ''
  return parseUtc(value)?.toLocaleString('zh-CN', {
    hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }) ?? ''
}

async function load() {
  loading.value = true
  try {
    const q = filter.value ? `?status=${filter.value}` : ''
    // 列表按当前筛选取；计数始终基于全量，避免筛选后徽标互相矛盾
    const [listResp, countResp, scheduleResp, alertResp] = await Promise.all([
      api.get(`/api/pipeline/jobs${q}`),
      api.get('/api/pipeline/summary'),
      api.get('/api/pipeline/ai-schedule'),
      api.get('/api/pipeline/alerts'),
    ])
    jobs.value = listResp.data
    if (openLogs.value !== null) await loadLogs(openLogs.value)
    aiSchedule.value = scheduleResp.data
    alerts.value = (alertResp.data.alerts || []).map(alertToOut)
    // Counts come from SQL aggregation, never from the first page of the list.
    const summary = countResp.data as Record<string, Record<string, number>>
    const sum = (status: string) => Object.values(summary).reduce((n, row) => n + Number(row[status] || 0), 0)
    const queued = sum('queued')
    const running = sum('running')
    counts.value = {
      queued,
      running,
      error: sum('error'),
      done: sum('done'),
      total: queued + running + sum('error') + sum('done'),
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
  refreshOnEvent = () => { if (!loading.value) load() }
  window.addEventListener('xsai:events-changed', refreshOnEvent)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (refreshOnEvent) window.removeEventListener('xsai:events-changed', refreshOnEvent)
})
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

/* AI 运行时段提示条 */
.pq-ai {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 9px 12px; border: 1px solid var(--line); background: var(--surface-2);
  font-size: var(--fs-sm); color: var(--ink-2);
}
.pq-ai.closed { border-color: color-mix(in oklab, var(--warn) 45%, var(--line)); background: var(--warn-soft); }
.pq-ai-next { min-width: 0; }
.pq-ai-link { margin-left: auto; color: var(--accent-2); font-weight: 600; }
.pq-held {
  color: color-mix(in oklab, var(--warn) 70%, var(--ink));
  border: 1px dashed color-mix(in oklab, var(--warn) 45%, var(--line));
  padding: 0 6px;
}

.pq-alerts { display: flex; flex-direction: column; gap: 6px; }
.pq-alert { display: flex; gap: 12px; align-items: flex-start; border: 1px solid var(--danger); background: var(--danger-soft); padding: 9px 12px; }
.pq-alert.warning { border-color: var(--warn); background: var(--warn-soft); }
.pq-alert-main { min-width: 0; flex: 1; }
.pq-alert-main b { color: var(--ink); }
.pq-alert-main p { margin: 3px 0; color: var(--ink-2); font-size: var(--fs-sm); overflow-wrap: anywhere; }
.pq-alert-count { margin-left: 6px; font-size: var(--fs-xs); color: var(--danger); font-weight: 700; }
.pq-alert-time { font-size: var(--fs-xs); color: var(--muted); font-variant-numeric: tabular-nums; }
.pq-alert-acts { display: flex; gap: 6px; flex: none; }

/* 任务列表 */
.pq-list { list-style: none; margin: 0; padding: 0; }
.pq-item {
  display: grid; grid-template-columns: 84px 1fr auto; gap: 14px;
  padding: 12px var(--sp-4);
  border-bottom: 1px solid var(--line-soft);
}
.pq-logs { grid-column: 2 / -1; border-top: 1px dashed var(--line-soft); margin-top: 8px; padding-top: 8px; }
.pq-log-empty { color: var(--muted); font-size: var(--fs-sm); }
.pq-log-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.pq-log { display: grid; grid-template-columns: 72px 48px minmax(0, 1fr) auto; gap: 8px; font-size: var(--fs-sm); }
.pq-log-time, .pq-log-level, .pq-log-actor { color: var(--muted); font-variant-numeric: tabular-nums; }
.pq-log.error .pq-log-level, .pq-log.error .pq-log-msg { color: var(--danger); }
.pq-log-msg { min-width: 0; overflow-wrap: anywhere; }
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
.pq-acts { gap: 6px; flex-wrap: wrap; }

@media (max-width: 860px) {
  .pq-item { grid-template-columns: 1fr; }
  .pq-pos { flex-direction: row; gap: 10px; }
  .pq-alert { flex-wrap: wrap; }
}
</style>
