<template>
  <div>
    <div class="filters">
      <el-select v-model="filters.platform" placeholder="平台" clearable style="width: 120px" @change="load">
        <el-option label="B站" value="bilibili" />
        <el-option label="抖音" value="douyin" />
        <el-option label="小红书" value="xhs" />
        <el-option label="微信公众号" value="wechat_mp" />
      </el-select>
      <el-select v-model="filters.transcribe_status" placeholder="转录状态" clearable style="width: 130px" @change="load">
        <el-option label="待转录" value="pending" />
        <el-option label="转录中" value="transcribing" />
        <el-option label="已完成" value="done" />
        <el-option label="失败" value="error" />
      </el-select>
      <el-button @click="load">刷新</el-button>
      <el-button @click="importDialog = true">导入文章</el-button>
      <el-button type="primary" :disabled="!selected.length" @click="exportSel('md')">导出 Markdown</el-button>
      <el-button type="primary" plain :disabled="!selected.length" @click="exportSel('json')">导出 JSON</el-button>
    </div>

    <el-table :data="assets" v-loading="loading" @selection-change="(s: any[]) => (selected = s)" style="margin-top: 12px">
      <el-table-column type="selection" width="44" />
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="platform" label="平台" width="90" />
      <el-table-column prop="author_name" label="作者" width="120" />
      <el-table-column label="下载" width="90">
        <template #default="{ row }"><el-tag size="small" :type="t(row.download_status)">{{ s(row.download_status) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="转录" width="90">
        <template #default="{ row }"><el-tag size="small" :type="t(row.transcribe_status)">{{ s(row.transcribe_status) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="分析" width="90">
        <template #default="{ row }"><el-tag size="small" :type="t(row.analyze_status)">{{ s(row.analyze_status) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openDetail(row.asset_id)">详情</el-button>
          <el-button size="small" @click="act(row.asset_id, 'transcribe')">转录</el-button>
          <el-button size="small" @click="act(row.asset_id, 'analyze')">分析</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="importDialog" title="导入微信公众号文章" width="520px">
      <el-input
        v-model="importUrl"
        placeholder="https://mp.weixin.qq.com/s/..."
        clearable
      />
      <p class="hint">仅支持单篇文章链接。批量历史文章列表接口微信已关闭，无法导入整号历史。</p>
      <template #footer>
        <el-button @click="importDialog = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="doImport">导入</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="drawer" title="素材详情" size="50%" @close="stopProcPoll">
      <div v-if="detail">
        <h3>{{ detail.content?.title }}</h3>
        <p class="meta">{{ detail.content?.platform }} · {{ detail.content?.author_name }}</p>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="文件目录">{{ detail.file?.full_dir || '—' }}</el-descriptions-item>
          <el-descriptions-item label="视频文件">{{ detail.file?.video_file || '—' }}</el-descriptions-item>
          <el-descriptions-item label="链接"><a :href="detail.content?.url" target="_blank">打开原页面</a></el-descriptions-item>
        </el-descriptions>

        <el-divider>转录文本</el-divider>
        <el-input v-if="detail.transcript" type="textarea" :rows="10" :model-value="detail.transcript.full_text" readonly />
        <el-empty v-else description="尚无转录" :image-size="60" />

        <el-divider>AI 分析</el-divider>
        <div v-if="detail.analysis">
          <p><b>摘要：</b>{{ detail.analysis.summary }}</p>
          <el-tag v-for="tag in detail.analysis.tags" :key="tag" style="margin-right: 6px">{{ tag }}</el-tag>
        </div>
        <el-empty v-else description="尚无分析" :image-size="60" />

        <el-divider>评论（按点赞排序）</el-divider>
        <p class="comment-total">评论总数 {{ detail?.metrics?.comment ?? '—' }}</p>
        <div v-if="comments.length">
          <div v-for="c in comments" :key="c.comment_id" class="comment">
            <div class="c-head">
              <b>{{ c.nickname || '匿名' }}</b>
              <span class="c-meta">{{ c.ip_location }} · 赞 {{ c.like_count }}<template v-if="fmtTime(c.created_at)"> · {{ fmtTime(c.created_at) }}</template></span>
            </div>
            <div class="c-body">{{ c.content }}</div>
          </div>
          <el-button v-if="comments.length < commentTotal" text @click="loadMoreComments">加载更多</el-button>
        </div>
        <p v-else class="comment-hint">
          该作品评论数或点赞数未达抓取门槛，仅显示数量。
        </p>

        <el-divider>分档加工</el-divider>
        <div class="tier-row">
          <el-tag size="small" type="info">当前档位：{{ detail.status?.tier || '未加工' }}</el-tag>
          <el-tag size="small" :type="t(proc.frames_status)">抽帧 {{ sStatus(proc.frames_status) }}</el-tag>
          <el-tag size="small" :type="t(proc.understand_status)">理解 {{ sStatus(proc.understand_status) }}</el-tag>
        </div>
        <div class="tier-actions">
          <el-button size="small" :loading="procBusy" :disabled="procBusy" @click="runProcess('l2')">抽帧（L2）</el-button>
          <el-button size="small" type="primary" :loading="procBusy" :disabled="procBusy" @click="runProcess('l3')">视频理解（L3）</el-button>
          <el-checkbox v-model="proc.use_cover">判类用封面</el-checkbox>
          <el-select v-model="proc.force_category" placeholder="改类型重跑（可选）" clearable size="small" style="width: 180px">
            <el-option v-for="(label, key) in taxonomy" :key="key" :label="label" :value="key" />
          </el-select>
          <el-button size="small" :loading="procBusy" :disabled="!proc.force_category || procBusy" @click="runReclassify">按类型重跑</el-button>
        </div>

        <div v-if="understanding" class="understand">
          <p>
            <b>判类：</b>
            <el-tag size="small" type="success">{{ taxonomy[understanding.category] || understanding.category }}</el-tag>
            <span class="c-meta">置信度 {{ (understanding.category_confidence * 100).toFixed(0) }}%</span>
            <el-tag v-if="understanding.low_confidence" size="small" type="warning">低置信度</el-tag>
          </p>
          <p class="c-meta">{{ understanding.category_reason }}</p>
          <p v-if="understanding.summary"><b>概述：</b>{{ understanding.summary }}</p>
          <div v-if="understanding.dimensions && Object.keys(understanding.dimensions).length" class="dims">
            <div v-for="(v, k) in understanding.dimensions" :key="k" class="dim">
              <span class="dim-k">{{ dimensionName(k) }}</span>
              <span class="dim-v">{{ fmtVal(v) }}</span>
            </div>
          </div>
          <p v-if="understanding.timeline?.length" class="c-meta">
            时间线：<template v-for="(e, i) in understanding.timeline" :key="i">{{ e.t }}s {{ e.event }}<template v-if="i < understanding.timeline.length - 1"> · </template></template>
          </p>
          <p class="c-meta">模型 {{ understanding.model }} · 版本 {{ understanding.prompt_version }}</p>
          <p v-if="understanding.error" class="err">{{ understanding.error }}</p>
        </div>
        <p v-else-if="proc.understand_status === 'error'" class="err">理解失败：{{ understandError || detail.status?.error }}</p>

        <div v-if="frames.length" class="frame-wall">
          <template v-for="f in frames" :key="f.scene_index">
            <img
              v-if="frameUrls[f.scene_index] && !failedFrames.has(f.scene_index)"
              :src="frameUrls[f.scene_index]"
              :title="`${f.timestamp_sec}s`"
              @error="onFrameError(f.scene_index)"
            />
          </template>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from '../api'

const assets = ref<any[]>([])
const selected = ref<any[]>([])
const loading = ref(false)
const drawer = ref(false)
const detail = ref<any>(null)
const filters = reactive({ platform: '', transcribe_status: '' })

const importDialog = ref(false)
const importUrl = ref('')
const importing = ref(false)

const comments = ref<any[]>([])
const commentTotal = ref(0)
const commentOffset = ref(0)

// 分档加工
const proc = reactive<any>({ frames_status: '', understand_status: '', use_cover: false, force_category: '' })
const frames = ref<any[]>([])
const frameUrls = ref<Record<number, string>>({})
const failedFrames = ref<Set<number>>(new Set())
const understanding = ref<any>(null)
const understandError = ref('')
const taxonomy = ref<Record<string, string>>({})
const commonDimensions = ref<any[]>([])
const dimensionNames = ref<Record<string, string>>({})
// 「判类默认用封面」全局默认值（来自设置页 process_use_cover），
// 抽屉打开时用它初始化勾选框，否则设置页开关没有消费方（回归修复）。
const globalUseCover = ref(false)
const procBusy = ref(false)

let procTimer: number | undefined

// 加工中状态：需轮询等待 worker 完成。
const ACTIVE_STATUS = ['pending', 'doing']
function isProcActive() {
  return ACTIVE_STATUS.includes(proc.frames_status) || ACTIVE_STATUS.includes(proc.understand_status)
}
function stopProcPoll() {
  if (procTimer) { clearInterval(procTimer); procTimer = undefined }
}
function syncProcTimer() {
  if (drawer.value && detail.value && isProcActive()) {
    if (!procTimer) procTimer = window.setInterval(() => loadProcess(detail.value.asset_id), 3000)
  } else {
    stopProcPoll()
  }
}

const procStatusText: Record<string, string> = {
  pending: '待处理', doing: '进行中', done: '完成', error: '失败',
}
function sStatus(v: string) { return procStatusText[v] ?? (v || '—') }

function dimensionName(key: string) {
  return dimensionNames.value[key] || key
}
function fmtVal(v: any) {
  if (v === null || v === undefined || v === '') return '—'
  return typeof v === 'object' ? JSON.stringify(v, null, 0) : String(v)
}

function revokeFrameUrls() {
  for (const url of Object.values(frameUrls.value)) URL.revokeObjectURL(url)
  frameUrls.value = {}
}

// 维度 key -> 中文名：合并通用维度 + 各类型专属维度。
function buildDimensionNames(common: any[], prompts: Record<string, any>) {
  const names: Record<string, string> = {}
  for (const d of common || []) if (d?.key) names[d.key] = d.name
  for (const spec of Object.values(prompts || {})) {
    for (const d of (spec as any)?.dimensions || []) if (d?.key) names[d.key] = d.name
  }
  dimensionNames.value = names
}

async function loadConfig(force = false) {
  if (!force && commonDimensions.value.length) return
  try {
    const { data } = await api.get('/api/system/process-settings')
    taxonomy.value = data.taxonomy || {}
    commonDimensions.value = data.common_dimensions || []
    buildDimensionNames(commonDimensions.value, data.type_prompts || {})
    globalUseCover.value = !!data.use_cover
  } catch { /* 设置不可用不阻塞详情 */ }
}

async function loadProcess(assetId: number, opts: { resetInputs?: boolean } = {}) {
  await loadConfig(opts.resetInputs)
  if (opts.resetInputs) {
    proc.use_cover = globalUseCover.value
    proc.force_category = ''
  }
  revokeFrameUrls()
  failedFrames.value = new Set()
  frames.value = []
  understanding.value = null
  understandError.value = ''
  const f = await api.get(`/api/assets/${assetId}/frames`)
  frames.value = f.data.frames || []
  proc.frames_status = f.data.frames_status
  // 档位也取自刷新点，避免动作后「当前档位」停留在打开抽屉时的快照。
  if (detail.value?.status) detail.value.status.tier = f.data.process_tier || ''
  const urls: Record<number, string> = {}
  for (const fr of frames.value) {
    try {
      const resp = await api.get(`/api/assets/${assetId}/frames/${fr.scene_index}/image`, { responseType: 'blob' })
      urls[fr.scene_index] = URL.createObjectURL(resp.data)
    } catch { /* 单帧缺失跳过 */ }
  }
  frameUrls.value = urls
  const u = await api.get(`/api/assets/${assetId}/understanding`)
  proc.understand_status = u.data.understand_status
  if (detail.value?.status && u.data.process_tier) detail.value.status.tier = u.data.process_tier
  understanding.value = u.data.status === 'pending' ? null : u.data
  understandError.value = u.data.error || ''
  syncProcTimer()
}

function onFrameError(sceneIndex: number) {
  const next = new Set(failedFrames.value)
  next.add(sceneIndex)
  failedFrames.value = next
}

async function runProcess(tier: string) {
  if (!detail.value || procBusy.value) return
  procBusy.value = true
  try {
    await api.post(`/api/assets/${detail.value.asset_id}/process`, {
      tier,
      force_category: proc.force_category || undefined,
      use_cover: proc.use_cover,
    })
    ElMessage.success('已加入队列')
    await loadProcess(detail.value.asset_id)
  } finally {
    procBusy.value = false
  }
}

async function runReclassify() {
  if (!detail.value || !proc.force_category || procBusy.value) return
  procBusy.value = true
  try {
    await api.post(`/api/assets/${detail.value.asset_id}/reclassify`, {
      force_category: proc.force_category,
      use_cover: proc.use_cover,
    })
    ElMessage.success('已按新类型重跑')
    await loadProcess(detail.value.asset_id)
  } finally {
    procBusy.value = false
  }
}

async function doImport() {
  if (!importUrl.value.trim()) return
  importing.value = true
  try {
    await api.post('/api/imports/wechat-article', { url: importUrl.value.trim() })
    ElMessage.success('导入成功')
    importDialog.value = false
    importUrl.value = ''
    await load()
  } finally {
    importing.value = false
  }
}

const statusText: Record<string, string> = {
  pending: '待处理', downloading: '下载中', transcribing: '转录中', analyzing: '分析中', done: '完成', error: '失败',
}
function s(v: string) { return statusText[v] ?? v }
function t(v: string) { return ({ done: 'success', error: 'danger' } as Record<string, string>)[v] ?? 'info' }
// 评论时间：后端为 UTC naive ISO（无 Z），补 'Z' 再按本地时区显示。
function fmtTime(v: string | null) {
  if (!v) return ''
  return new Date(v.endsWith('Z') ? v : v + 'Z').toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/api/assets', { params: { ...filters } })
    assets.value = data
  } finally {
    loading.value = false
  }
}

async function openDetail(id: number) {
  const { data } = await api.get(`/api/assets/${id}`)
  detail.value = data
  drawer.value = true
  await loadComments(id)
  await loadProcess(id, { resetInputs: true })
}

async function loadComments(assetId: number, reset = true) {
  if (reset) {
    comments.value = []
    commentOffset.value = 0
  }
  const { data } = await api.get(`/api/assets/${assetId}/comments`, {
    params: { limit: 50, offset: commentOffset.value },
  })
  comments.value = reset ? data.comments : [...comments.value, ...data.comments]
  commentTotal.value = data.total
  commentOffset.value = comments.value.length
}

async function loadMoreComments() {
  if (detail.value) await loadComments(detail.value.asset_id, false)
}

async function act(id: number, kind: string) {
  await api.post(`/api/assets/${id}/${kind}`)
  ElMessage.success('已加入队列')
  await load()
}

async function exportSel(format: string) {
  const ids = selected.value.map((a) => a.asset_id)
  const { data } = await api.post('/api/assets/export', { asset_ids: ids, format })
  const content = format === 'json' ? JSON.stringify(data, null, 2) : data.content
  const blob = new Blob([content], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `assets.${format}`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(load)
onUnmounted(() => {
  stopProcPoll()
  revokeFrameUrls()
})
</script>

<style scoped>
.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.meta { color: #909399; margin: 4px 0 12px; }
.hint { color: #909399; font-size: 12px; margin: 8px 0 0; }
.comment { padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.c-head { display: flex; justify-content: space-between; }
.c-meta { color: #909399; font-size: 12px; }
.c-body { margin-top: 4px; font-size: 13px; }
.comment-total { color: #606266; font-size: 12px; margin: 0 0 4px; }
.comment-hint { color: #909399; font-size: 12px; }
.tier-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tier-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 10px 0; }
.understand { background: #fafafa; border: 1px solid #f0f0f0; border-radius: 6px; padding: 10px; font-size: 13px; }
.dims { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.dim { display: flex; gap: 8px; }
.dim-k { color: #909399; min-width: 88px; }
.dim-v { flex: 1; }
.err { color: #f56c6c; font-size: 12px; }
.frame-wall { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.frame-wall img { width: 96px; height: 64px; object-fit: cover; border-radius: 4px; border: 1px solid #f0f0f0; }
</style>
