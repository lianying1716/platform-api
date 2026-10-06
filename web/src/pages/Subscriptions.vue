<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建订阅</el-button>
      <el-button @click="load">刷新</el-button>
      <span class="hint">订阅按周期自动同步；B站 / 抖音 支持从远端拉取收藏夹、合集与关注列表。</span>
    </div>

    <el-table :data="subs" v-loading="loading" style="margin-top: 12px">
      <el-table-column label="平台" width="90">
        <template #default="{ row }">{{ platformLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="来源" min-width="200">
        <template #default="{ row }">
          <div class="src">
            <span class="src-name">{{ row.title || row.remote_id }}</span>
            <el-tag size="small" type="info" effect="plain">{{ sourceText(row.source_type) }}</el-tag>
          </div>
          <div class="src-id">{{ row.remote_id }}</div>
        </template>
      </el-table-column>
      <el-table-column label="自动下载" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.auto_download ? 'success' : 'info'">{{ row.auto_download ? '是' : '否' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="周期" width="100">
        <template #default="{ row }">{{ row.interval_minutes }} 分钟</template>
      </el-table-column>
      <el-table-column label="筛选" min-width="190">
        <template #default="{ row }">{{ filterSummary(row) }}</template>
      </el-table-column>
      <el-table-column label="画质" width="90">
        <template #default="{ row }">{{ qualityText(row.download_quality) }}</template>
      </el-table-column>
      <el-table-column label="上次同步" width="170">
        <template #default="{ row }">{{ fmt(row.last_sync_at) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" :loading="toggling === row.id" @change="toggle(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :loading="syncing === row.id" @click="sync(row)">立即同步</el-button>
          <el-button size="small" @click="openRuns(row)">历史</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-popconfirm title="删除该订阅？同步历史会一并删除" @confirm="remove(row)">
            <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建 / 编辑订阅（同一个弹窗） -->
    <el-dialog v-model="dialog" :title="editing ? '编辑订阅' : '新建订阅'" width="580px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item label="账号">
          <el-select v-model="form.account_id" placeholder="选择已登录账号" style="width: 100%" @change="onAccountChange">
            <el-option v-for="a in accounts" :key="a.id" :label="`${a.platform_label} - ${a.display_name || a.id}`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源类型">
          <el-select v-model="form.source_type" style="width: 100%" @change="onSourceTypeChange">
            <el-option label="收藏夹" value="favorite" />
            <el-option label="合集" value="collection" />
            <el-option label="关注博主" value="creator" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <div style="width: 100%">
            <el-radio-group v-model="pickMode" size="small" style="margin-bottom: 6px" @change="onPickModeChange">
              <el-radio-button value="list">从列表选择</el-radio-button>
              <el-radio-button value="manual">手动填 ID</el-radio-button>
            </el-radio-group>
            <el-select
              v-if="pickMode === 'list'"
              v-model="form.remote_id"
              :loading="sourcesLoading"
              :no-data-text="sourceEmptyText"
              filterable
              placeholder="选择要跟踪的收藏夹 / 合集 / 博主"
              style="width: 100%"
            >
              <el-option v-for="s in sources" :key="s.remote_id" :label="s.title || s.remote_id" :value="s.remote_id" />
            </el-select>
            <el-input v-else v-model="form.remote_id" placeholder="收藏夹ID / 合集ID / UP主mid / 博主sec_uid" />
          </div>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.title" placeholder="例如：技术收藏夹（可留空）" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
        <el-form-item label="自动下载"><el-switch v-model="form.auto_download" /></el-form-item>
        <el-form-item label="周期(分钟)"><el-input-number v-model="form.interval_minutes" :min="10" :step="10" /></el-form-item>
        <el-form-item label="最低播放量">
          <el-input-number v-model="form.min_play" :min="0" :step="1000" />
          <span class="detail">0 = 不限</span>
        </el-form-item>
        <el-form-item label="最近天数">
          <el-input-number v-model="form.publish_after_days" :min="0" />
          <span class="detail">只同步最近 N 天发布的内容，0 = 不限</span>
        </el-form-item>
        <el-form-item label="单次上限">
          <el-input-number v-model="form.max_items" :min="1" :max="500" />
          <span class="detail">单次同步最多入库条数（1~500）</span>
        </el-form-item>
        <el-form-item label="只同步新"><el-switch v-model="form.only_new" /></el-form-item>
        <el-form-item label="下载画质">
          <el-select v-model="form.download_quality" style="width: 200px">
            <el-option label="最佳画质" value="best" />
            <el-option label="1080P" value="1080" />
            <el-option label="720P" value="720" />
            <el-option label="仅音频" value="audio" />
          </el-select>
        </el-form-item>
        <el-form-item label="同步评论"><el-switch v-model="form.with_comments" /></el-form-item>
        <el-form-item label="子目录模板">
          <el-input v-model="form.subdir_template" placeholder="{platform}/{creator}/{date}" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 同步历史 -->
    <el-drawer v-model="runsDrawer" :title="`同步历史 · ${runsTitle}`" size="50%">
      <el-table :data="runs" v-loading="runsLoading">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ fmt(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="runStatusType(row.status)">{{ runStatusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fetched" label="抓取" width="80" />
        <el-table-column prop="added" label="新增" width="80" />
        <el-table-column prop="error" label="错误" min-width="160" show-overflow-tooltip />
      </el-table>
      <el-empty v-if="!runsLoading && !runs.length" description="暂无同步记录" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { api, type Account } from '../api'

interface RemoteSource { remote_id: string; title: string }

interface Subscription {
  id: number
  account_id: number
  platform: string
  source_type: string
  remote_id: string
  title: string
  enabled: boolean
  auto_download: boolean
  interval_minutes: number
  subdir_template: string
  min_play: number
  publish_after_days: number
  max_items: number
  only_new: boolean
  download_quality: string
  with_comments: boolean
  last_sync_at: string | null
}

interface SyncRunRow {
  id: number
  started_at: string | null
  finished_at: string | null
  status: string
  fetched: number
  added: number
  error: string
}

const SOURCE_TYPES: Record<string, string> = { favorite: '收藏夹', collection: '合集', creator: '关注博主', like: '点赞' }
const QUALITY_LABELS: Record<string, string> = { best: '最佳', '1080': '1080P', '720': '720P', audio: '仅音频' }
const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音', bilibili: 'B站', xhs: '小红书', kuaishou: '快手', weibo: '微博', tieba: '贴吧', zhihu: '知乎',
}
const DEFAULT_SUBDIR = '{platform}/{creator}/{date}'

const subs = ref<Subscription[]>([])
const accounts = ref<Account[]>([])
const loading = ref(false)
const syncing = ref<number | null>(null)
const toggling = ref<number | null>(null)

const dialog = ref(false)
const editing = ref<Subscription | null>(null)
const submitting = ref(false)
const pickMode = ref<'list' | 'manual'>('list')
const sources = ref<RemoteSource[]>([])
const sourcesLoading = ref(false)
const sourcesError = ref('')
let sourcesSeq = 0

const runsDrawer = ref(false)
const runsTitle = ref('')
const runs = ref<SyncRunRow[]>([])
const runsLoading = ref(false)
let runsSubId: number | null = null

const form = reactive({
  account_id: null as number | null,
  source_type: 'favorite',
  remote_id: '',
  title: '',
  enabled: true,
  auto_download: false,
  interval_minutes: 360,
  subdir_template: DEFAULT_SUBDIR,
  min_play: 0,
  publish_after_days: 0,
  max_items: 50,
  only_new: true,
  download_quality: 'best',
  with_comments: false,
})

const sourceEmptyText = computed(() => {
  if (!form.account_id) return '请先选择账号'
  if (sourcesError.value) return sourcesError.value
  return '该账号在此类型下没有可订阅的源'
})

function sourceText(v: string) { return SOURCE_TYPES[v] ?? v }
function qualityText(v: string) { return QUALITY_LABELS[v] ?? v }
function platformLabel(row: Subscription) {
  const acc = accounts.value.find((a) => a.id === row.account_id)
  return acc?.platform_label || PLATFORM_LABELS[row.platform] || row.platform
}
function fmt(v: string | null) {
  if (!v) return '从未'
  return new Date(v.endsWith('Z') ? v : v + 'Z').toLocaleString('zh-CN', { hour12: false })
}
function compact(n: number) {
  return n >= 10000 ? `${(n / 10000).toFixed(n % 10000 === 0 ? 0 : 1)}万` : `${n}`
}
function filterSummary(row: Subscription) {
  const play = row.min_play > 0 ? `播放≥${compact(row.min_play)}` : '播放不限'
  const days = row.publish_after_days > 0 ? `最近${row.publish_after_days}天` : '时间不限'
  return `${play} · ${days} · 上限${row.max_items}`
}
function runStatusType(s: string) {
  return ({ success: 'success', error: 'danger' } as Record<string, string>)[s] ?? 'warning'
}
function runStatusText(s: string) {
  return ({ success: '成功', error: '失败', running: '进行中' } as Record<string, string>)[s] ?? s
}

async function load() {
  loading.value = true
  try {
    subs.value = (await api.get('/api/subscriptions')).data
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    account_id: null,
    source_type: 'favorite',
    remote_id: '',
    title: '',
    enabled: true,
    auto_download: false,
    interval_minutes: 360,
    subdir_template: DEFAULT_SUBDIR,
    min_play: 0,
    publish_after_days: 0,
    max_items: 50,
    only_new: true,
    download_quality: 'best',
    with_comments: false,
  })
  sources.value = []
  sourcesError.value = ''
}

function openCreate() {
  editing.value = null
  resetForm()
  pickMode.value = 'list'
  dialog.value = true
}

function openEdit(row: Subscription) {
  editing.value = row
  Object.assign(form, {
    account_id: row.account_id,
    source_type: row.source_type,
    remote_id: row.remote_id,
    title: row.title,
    enabled: row.enabled,
    auto_download: row.auto_download,
    interval_minutes: row.interval_minutes,
    subdir_template: row.subdir_template || DEFAULT_SUBDIR,
    min_play: row.min_play,
    publish_after_days: row.publish_after_days,
    max_items: row.max_items,
    only_new: row.only_new,
    download_quality: row.download_quality,
    with_comments: row.with_comments,
  })
  // 编辑时默认手动模式，避免为已选来源发起一次远端请求；切到「从列表选择」再拉取。
  pickMode.value = 'manual'
  sources.value = []
  sourcesError.value = ''
  dialog.value = true
}

function onAccountChange() {
  form.remote_id = ''
  sources.value = []
  sourcesError.value = ''
  if (pickMode.value === 'list') loadSources()
}

function onSourceTypeChange() {
  form.remote_id = ''
  sources.value = []
  sourcesError.value = ''
  if (pickMode.value === 'list') loadSources()
}

function onPickModeChange() {
  if (pickMode.value === 'list') loadSources()
}

async function loadSources() {
  if (!form.account_id) {
    sources.value = []
    return
  }
  const seq = ++sourcesSeq
  sourcesLoading.value = true
  sourcesError.value = ''
  try {
    const { data } = await api.get(`/api/accounts/${form.account_id}/remote-sources`, {
      params: { source_type: form.source_type },
    })
    if (seq !== sourcesSeq) return
    sources.value = Array.isArray(data) ? data : []
    if (!sources.value.length) sourcesError.value = '没拉到可订阅的源，可切「手动填 ID」'
  } catch {
    if (seq !== sourcesSeq) return
    sources.value = []
    sourcesError.value = '加载失败，可切「手动填 ID」'
  } finally {
    if (seq === sourcesSeq) sourcesLoading.value = false
  }
}

function validate(): string | null {
  if (!form.account_id) return '请选择账号'
  if (!form.remote_id || !form.remote_id.trim()) return pickMode.value === 'list' ? '请选择来源' : '请填写来源 ID'
  const interval = Number(form.interval_minutes)
  if (!Number.isFinite(interval) || interval < 10) return '同步周期至少 10 分钟'
  const minPlay = Number(form.min_play)
  if (!Number.isFinite(minPlay) || minPlay < 0) return '最低播放量不能小于 0'
  const days = Number(form.publish_after_days)
  if (!Number.isFinite(days) || days < 0) return '最近天数不能小于 0'
  const maxItems = Number(form.max_items)
  if (!Number.isFinite(maxItems) || maxItems < 1 || maxItems > 500) return '单次上限需在 1~500 之间'
  if (!(form.download_quality in QUALITY_LABELS)) return '下载画质不合法'
  return null
}

async function submit() {
  const err = validate()
  if (err) {
    ElMessage.warning(err)
    return
  }
  submitting.value = true
  try {
    const payload = { ...form, remote_id: form.remote_id.trim() }
    if (editing.value) {
      await api.put(`/api/subscriptions/${editing.value.id}`, payload)
      ElMessage.success('已保存')
    } else {
      await api.post('/api/subscriptions', payload)
      ElMessage.success('已创建')
    }
    dialog.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

function rowPayload(row: Subscription, patch: Partial<Subscription> = {}) {
  return {
    account_id: row.account_id,
    source_type: row.source_type,
    remote_id: row.remote_id,
    title: row.title,
    enabled: row.enabled,
    auto_download: row.auto_download,
    interval_minutes: row.interval_minutes,
    subdir_template: row.subdir_template,
    min_play: row.min_play,
    publish_after_days: row.publish_after_days,
    max_items: row.max_items,
    only_new: row.only_new,
    download_quality: row.download_quality,
    with_comments: row.with_comments,
    ...patch,
  }
}

async function sync(row: Subscription) {
  syncing.value = row.id
  try {
    const { data } = await api.post(`/api/subscriptions/${row.id}/sync`)
    if (data.status === 'success' && !data.error) {
      ElMessage.success(`同步完成：抓取 ${data.fetched} 条，新增 ${data.added} 条`)
    } else {
      ElMessage({ message: data.error || `同步未完成（${data.status}）`, type: 'warning' })
    }
    await load()
    if (runsDrawer.value && runsSubId === row.id) await loadRuns(row.id)
  } finally {
    syncing.value = null
  }
}

async function toggle(row: Subscription) {
  toggling.value = row.id
  try {
    await api.put(`/api/subscriptions/${row.id}`, rowPayload(row, { enabled: !row.enabled }))
    await load()
  } finally {
    toggling.value = null
  }
}

async function remove(row: Subscription) {
  await api.delete(`/api/subscriptions/${row.id}`)
  ElMessage.success('已删除')
  if (runsSubId === row.id) runsDrawer.value = false
  await load()
}

async function loadRuns(subId: number) {
  runsLoading.value = true
  try {
    runs.value = (await api.get(`/api/subscriptions/${subId}/runs`)).data
  } finally {
    runsLoading.value = false
  }
}

async function openRuns(row: Subscription) {
  runsSubId = row.id
  runsTitle.value = row.title || row.remote_id
  runs.value = []
  runsDrawer.value = true
  await loadRuns(row.id)
}

onMounted(async () => {
  accounts.value = (await api.get('/api/accounts')).data
  await load()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
}

.hint {
  font-size: 12px;
  color: #909399;
}

.src {
  display: flex;
  align-items: center;
  gap: 6px;
}

.src-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.src-id {
  font-size: 12px;
  color: #a8abb2;
}

.detail {
  margin-left: 8px;
  font-size: 12px;
  color: #909399;
}
</style>
