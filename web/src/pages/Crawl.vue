<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>关键词采集</h1>
      <span class="sub">按关键词发现作品 · 支持门槛触发评论抓取</span>
    </div>

    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建采集任务</el-button>
      <el-button @click="load">刷新</el-button>
      <span class="hint">关键词采集依赖 MediaCrawler（一期 CLI 子进程），需在系统设置里配置其路径并完成对应平台登录。</span>
    </div>

    <el-table :data="tasks" v-loading="loading" style="margin-top: 14px">
      <el-table-column label="平台" width="90">
        <template #default="{ row }">{{ platformLabel(row.platform) }}</template>
      </el-table-column>
      <el-table-column prop="keyword" label="关键词" min-width="140" />
      <el-table-column label="排序" width="90">
        <template #default="{ row }">{{ row.sort === 'popular' ? '热门' : '最新' }}</template>
      </el-table-column>
      <el-table-column prop="limit_per_run" label="单次上限" width="90" />
      <el-table-column label="周期" width="110">
        <template #default="{ row }">{{ intervalText(row.interval_minutes) }}</template>
      </el-table-column>
      <el-table-column label="进候选池" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.to_candidate ? 'success' : 'info'">{{ row.to_candidate ? '是' : '否' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggle(row)" />
        </template>
      </el-table-column>
      <el-table-column label="上次运行" width="160">
        <template #default="{ row }">{{ fmt(row.last_run_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :loading="running === row.id" @click="run(row)">立即运行</el-button>
          <el-button size="small" @click="openRuns(row)">运行记录</el-button>
          <el-popconfirm title="确定删除该任务？" @confirm="remove(row)">
            <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" :title="editing ? '编辑任务' : '新建采集任务'" width="520px" destroy-on-close>
      <el-form label-width="100px">
        <el-form-item label="平台">
          <el-select v-model="form.platform">
            <el-option v-for="p in platforms" :key="p.value" :label="p.label" :value="p.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="form.keyword" placeholder="例如：露营装备" />
        </el-form-item>
        <el-form-item label="排序">
          <el-radio-group v-model="form.sort">
            <el-radio-button value="popular">热门</el-radio-button>
            <el-radio-button value="latest">最新</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="单次上限">
          <el-input-number v-model="form.limit_per_run" :min="1" :max="500" />
        </el-form-item>
        <el-form-item label="最低播放量">
          <el-input-number v-model="form.min_play" :min="0" :step="1000" />
        </el-form-item>
        <el-form-item label="采集周期">
          <el-select v-model="form.interval_minutes">
            <el-option label="每小时" :value="60" />
            <el-option label="每 6 小时" :value="360" />
            <el-option label="每天" :value="1440" />
            <el-option label="每周" :value="10080" />
          </el-select>
        </el-form-item>
        <el-form-item label="抓评论">
          <el-switch v-model="form.with_comments" />
        </el-form-item>
        <el-form-item label="视频进候选池">
          <el-switch v-model="form.to_candidate" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="runsDrawer" title="运行记录" size="50%">
      <el-table :data="runs" v-loading="runsLoading">
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'success' ? 'success' : row.status === 'error' ? 'danger' : 'warning'">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fetched" label="抓取" width="80" />
        <el-table-column prop="added" label="新增" width="80" />
        <el-table-column prop="error" label="错误" min-width="160" show-overflow-tooltip />
        <el-table-column label="开始时间" width="160">
          <template #default="{ row }">{{ fmt(row.started_at) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const platforms = [
  { value: 'xhs', label: '小红书' },
  { value: 'douyin', label: '抖音' },
  { value: 'bilibili', label: 'B站' },
  { value: 'kuaishou', label: '快手' },
  { value: 'weibo', label: '微博' },
  { value: 'tieba', label: '贴吧' },
  { value: 'zhihu', label: '知乎' },
]

interface CrawlTask {
  id: number
  platform: string
  keyword: string
  sort: string
  limit_per_run: number
  with_comments: boolean
  to_candidate: boolean
  min_play: number
  interval_minutes: number
  enabled: boolean
  last_run_at: string | null
}

const tasks = ref<CrawlTask[]>([])
const loading = ref(false)
const running = ref<number | null>(null)
const dialog = ref(false)
const submitting = ref(false)
const editing = ref<CrawlTask | null>(null)
const runsDrawer = ref(false)
const runs = ref<any[]>([])
const runsLoading = ref(false)

const form = reactive({
  platform: 'xhs',
  keyword: '',
  sort: 'popular',
  limit_per_run: 50,
  with_comments: false,
  to_candidate: true,
  min_play: 0,
  interval_minutes: 1440,
  enabled: true,
})

function platformLabel(v: string) {
  return platforms.find((p) => p.value === v)?.label ?? v
}
function intervalText(m: number) {
  return ({ 60: '每小时', 360: '每 6 小时', 1440: '每天', 10080: '每周' } as Record<number, string>)[m] ?? `${m} 分钟`
}
function fmt(v: string | null) {
  if (!v) return '—'
  return new Date(v.endsWith('Z') ? v : v + 'Z').toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/api/crawl-tasks')
    tasks.value = data
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    platform: 'xhs', keyword: '', sort: 'popular', limit_per_run: 50,
    with_comments: false, to_candidate: true, min_play: 0, interval_minutes: 1440, enabled: true,
  })
  dialog.value = true
}

async function submit() {
  if (!form.keyword.trim()) {
    ElMessage.warning('请填写关键词')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      await api.put(`/api/crawl-tasks/${editing.value.id}`, form)
    } else {
      await api.post('/api/crawl-tasks', form)
    }
    ElMessage.success('已保存')
    dialog.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

async function toggle(row: CrawlTask) {
  await api.put(`/api/crawl-tasks/${row.id}`, { ...row })
}

async function run(row: CrawlTask) {
  running.value = row.id
  try {
    await api.post(`/api/crawl-tasks/${row.id}/run`)
    ElMessage.success('已加入采集队列')
  } finally {
    running.value = null
  }
}

async function remove(row: CrawlTask) {
  await api.delete(`/api/crawl-tasks/${row.id}`)
  ElMessage.success('已删除')
  await load()
}

async function openRuns(row: CrawlTask) {
  runsDrawer.value = true
  runsLoading.value = true
  try {
    const { data } = await api.get(`/api/crawl-tasks/${row.id}/runs`)
    runs.value = data
  } finally {
    runsLoading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; gap: 8px; align-items: center; }
.hint { color: #909399; font-size: 12px; margin-left: 8px; }
</style>
