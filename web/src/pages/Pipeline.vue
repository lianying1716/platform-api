<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>处理队列</h1>
      <span class="sub">下载 / 转录 / 视频理解任务</span>
    </div>

    <div class="toolbar">
      <el-radio-group v-model="filter" @change="load">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="queued">排队中</el-radio-button>
        <el-radio-button value="running">执行中</el-radio-button>
        <el-radio-button value="error">失败</el-radio-button>
        <el-radio-button value="done">已完成</el-radio-button>
      </el-radio-group>
      <el-button @click="load">刷新</el-button>
      <el-button v-if="filter === 'error'" type="warning" @click="retryAll">全部重试</el-button>
    </div>

    <el-table :data="jobs" v-loading="loading" style="margin-top: 12px">
      <el-table-column prop="id" label="#" width="70" />
      <el-table-column label="类型" width="110">
        <template #default="{ row }">{{ typeText(row.job_type) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="尝试" width="80">
        <template #default="{ row }">{{ row.attempts }}/{{ row.max_attempts }}</template>
      </el-table-column>
      <el-table-column prop="error" label="错误" min-width="200" show-overflow-tooltip />
      <el-table-column label="更新时间" width="170">
        <template #default="{ row }">{{ fmt(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'error'" size="small" @click="retry(row)">重试</el-button>
        </template>
      </el-table-column>
    </el-table>
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

function typeText(v: string) { return ({ download: '下载', transcribe: '转录', analyze: '分析', sync: '同步', crawl: '采集' } as Record<string, string>)[v] ?? v }
function statusText(v: string) { return ({ queued: '排队中', running: '执行中', done: '已完成', error: '失败' } as Record<string, string>)[v] ?? v }
function statusType(v: string) { return ({ queued: 'info', running: 'warning', done: 'success', error: 'danger' } as Record<string, string>)[v] ?? 'info' }
function fmt(v: string | null) { return v ? new Date(v + 'Z').toLocaleString('zh-CN', { hour12: false }) : '—' }

async function load() {
  loading.value = true
  try {
    const q = filter.value ? `?status=${filter.value}` : ''
    jobs.value = (await api.get(`/api/pipeline/jobs${q}`)).data
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
  await api.post('/api/pipeline/jobs/retry-all')
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
.toolbar { display: flex; gap: 8px; align-items: center; }
</style>
