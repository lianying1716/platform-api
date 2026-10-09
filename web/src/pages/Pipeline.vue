<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>处理队列</h1>
      <span class="sub">下载 / 转录 / 视频理解任务</span>
    </div>

    <div class="toolbar">
      <div class="ui-seg">
        <button v-for="f in filters" :key="f.v" :class="{ on: filter === f.v }" @click="setFilter(f.v)">
          {{ f.label }}
        </button>
      </div>
      <button class="ui-btn" :disabled="loading" @click="load">刷新</button>
      <button v-if="filter === 'error'" class="ui-btn danger" @click="retryAll">全部重试</button>
      <span class="grow" />
      <span class="ui-badge" :class="{ acc: counts.running > 0 }">运行中 {{ counts.running }}</span>
      <span class="ui-badge" :class="{ bad: counts.error > 0 }">失败 {{ counts.error }}</span>
    </div>

    <section class="ui-panel" v-loading="loading">
      <div v-if="!loading && !jobs.length" class="ui-empty">
        <div class="big">队列是空的</div>
        <p>发起采集、同步或加工后，任务会出现在这里。</p>
      </div>
      <table v-else class="ui-table">
        <thead>
          <tr>
            <th style="width: 64px">#</th>
            <th style="width: 100px">类型</th>
            <th style="width: 100px">状态</th>
            <th style="width: 80px">尝试</th>
            <th>错误</th>
            <th style="width: 170px">更新时间</th>
            <th style="width: 90px; text-align: right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in jobs" :key="row.id">
            <td class="num">{{ row.id }}</td>
            <td class="strong">{{ typeText(row.job_type) }}</td>
            <td><span class="ui-badge" :class="statusTone(row.status)">{{ statusText(row.status) }}</span></td>
            <td class="num">{{ row.attempts }}/{{ row.max_attempts }}</td>
            <td class="ellip err">{{ row.error || '—' }}</td>
            <td class="num">{{ fmt(row.updated_at) }}</td>
            <td style="text-align: right">
              <button v-if="row.status === 'error'" class="ui-btn ghost sm" @click="retry(row)">重试</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

const jobs = ref<any[]>([])
const loading = ref(false)
const filter = ref('')
let timer: number | undefined

const filters = [
  { v: '', label: '全部' },
  { v: 'queued', label: '排队中' },
  { v: 'running', label: '执行中' },
  { v: 'error', label: '失败' },
  { v: 'done', label: '已完成' },
]
const counts = ref({ running: 0, error: 0 })

function setFilter(v: string) {
  filter.value = v
  load()
}

function typeText(v: string) { return ({ download: '下载', transcribe: '转录', analyze: '分析', sync: '同步', crawl: '采集' } as Record<string, string>)[v] ?? v }
function statusText(v: string) { return ({ queued: '排队中', running: '执行中', done: '已完成', error: '失败' } as Record<string, string>)[v] ?? v }
function statusTone(v: string) { return ({ queued: '', running: 'warn', done: 'ok', error: 'bad' } as Record<string, string>)[v] ?? '' }
function fmt(v: string | null) { return v ? new Date(v + 'Z').toLocaleString('zh-CN', { hour12: false }) : '—' }

async function load() {
  loading.value = true
  try {
    const q = filter.value ? `?status=${filter.value}` : ''
    // 列表按当前筛选取；徽标计数始终基于全量，避免筛选后互相矛盾
    const [listResp, allResp] = await Promise.all([
      api.get(`/api/pipeline/jobs${q}`),
      filter.value ? api.get('/api/pipeline/jobs') : null,
    ])
    jobs.value = listResp.data
    const all = (allResp?.data ?? listResp.data) as any[]
    counts.value = {
      running: all.filter((j) => j.status === 'running' || j.status === 'queued').length,
      error: all.filter((j) => j.status === 'error').length,
    }
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
.toolbar { display: flex; gap: var(--sp-3); align-items: center; flex-wrap: wrap; }
.toolbar .grow { margin-left: auto; }
.ellip { max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.err { color: var(--danger); }
</style>
