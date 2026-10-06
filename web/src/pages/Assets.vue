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

    <el-drawer v-model="drawer" title="素材详情" size="50%" @close="closeDrawer">
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
          <el-tag size="small" type="info">当前档位：{{ tierLabel(detail.status?.tier) }}</el-tag>
          <el-tag size="small" :type="t(proc.understand_status)">理解 {{ sStatus(proc.understand_status) }}</el-tag>
        </div>
        <div class="tier-actions">
          <el-button size="small" :loading="procBusy" :disabled="procBusy" @click="runProcess('basic')">转录（普通）</el-button>
          <el-button size="small" type="primary" :loading="procBusy" :disabled="procBusy" @click="runProcess('advanced')">视频理解（高级）</el-button>
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

        <el-divider>爆款拆解</el-divider>
        <div class="bd-actions">
          <el-button size="small" type="primary" :loading="bdBusy" :disabled="bdBusy || isBdActive()" @click="runBreakdown(!!breakdown)">
            {{ breakdown ? '重新拆解' : '生成爆款拆解' }}
          </el-button>
          <el-tag v-if="isBdActive()" size="small" type="warning">拆解中…</el-tag>
          <el-tag v-else-if="bdStatus === 'error'" size="small" type="danger">失败</el-tag>
          <span class="c-meta">复用转录 / 视频理解 / 评论 / 指标，产出「为什么火 + 怎么复刻」</span>
        </div>
        <p v-if="bdStatus === 'error' && bdError" class="err">{{ bdError }}</p>

        <div v-if="breakdown" class="bd-report">
          <div class="bd-head">
            <el-progress type="dashboard" :percentage="Math.round(breakdown.score || 0)" :width="96" />
            <div class="bd-verdict">
              <p class="bd-verdict-text">{{ breakdown.verdict || '（无结论）' }}</p>
              <p class="c-meta">
                模型 {{ breakdown.model || '—' }} · 提示词 {{ breakdown.prompt_version || '—' }}
                <el-tag v-if="breakdown.understanding_available === false" size="small" type="warning">无视频理解（降级报告）</el-tag>
              </p>
            </div>
          </div>

          <div v-if="breakdown.sections?.hook" class="bd-block">
            <h4>开场钩子</h4>
            <p v-if="breakdown.sections.hook.first_line"><b>首句台词：</b>{{ breakdown.sections.hook.first_line }}</p>
            <p v-if="breakdown.sections.hook.first_frame"><b>首帧画面：</b>{{ breakdown.sections.hook.first_frame }}</p>
            <p>
              <el-tag v-if="breakdown.sections.hook.hook_type" size="small">{{ breakdown.sections.hook.hook_type }}</el-tag>
              <span class="c-meta">{{ breakdown.sections.hook.why }}</span>
            </p>
          </div>

          <div v-if="breakdown.sections?.structure?.length" class="bd-block">
            <h4>内容结构</h4>
            <el-table :data="breakdown.sections.structure" size="small" border>
              <el-table-column prop="segment" label="时间段" width="110" />
              <el-table-column prop="role" label="作用" width="90" />
              <el-table-column prop="summary" label="概述" show-overflow-tooltip />
            </el-table>
          </div>

          <div v-if="breakdown.sections?.pacing" class="bd-block">
            <h4>节奏</h4>
            <p v-if="breakdown.sections.pacing.emotion_curve"><b>情绪曲线：</b>{{ breakdown.sections.pacing.emotion_curve }}</p>
            <p v-if="breakdown.sections.pacing.pattern_interrupts?.length">
              <b>变化点：</b>
              <template v-for="(x, i) in breakdown.sections.pacing.pattern_interrupts" :key="i">{{ x }}<template v-if="i < breakdown.sections.pacing.pattern_interrupts.length - 1">；</template></template>
            </p>
            <p v-if="breakdown.sections.pacing.re_engagement_points?.length">
              <b>二次拉回：</b>{{ breakdown.sections.pacing.re_engagement_points.join('、') }} 秒
            </p>
          </div>

          <div v-if="breakdown.sections?.script" class="bd-block">
            <h4>文案</h4>
            <p v-if="breakdown.sections.script.money_lines?.length"><b>金句：</b>{{ breakdown.sections.script.money_lines.join('；') }}</p>
            <p v-if="breakdown.sections.script.reusable_phrases?.length"><b>可复用句式：</b>{{ breakdown.sections.script.reusable_phrases.join('；') }}</p>
          </div>

          <div v-if="breakdown.sections?.psych_triggers?.length" class="bd-block">
            <h4>心理触发</h4>
            <el-tag v-for="(p, i) in breakdown.sections.psych_triggers" :key="i" size="small" style="margin-right: 6px">{{ p }}</el-tag>
          </div>

          <div v-if="breakdown.sections?.cta" class="bd-block">
            <h4>行动号召（CTA）</h4>
            <p>
              <el-tag v-if="breakdown.sections.cta.type" size="small">{{ breakdown.sections.cta.type }}</el-tag>
              <span v-if="breakdown.sections.cta.placement" class="c-meta">位置：{{ breakdown.sections.cta.placement }}</span>
            </p>
          </div>

          <div v-if="hasCommentInsights" class="bd-block">
            <h4>评论洞察</h4>
            <p v-if="breakdown.comment_insights.top_themes?.length"><b>热门话题：</b>{{ breakdown.comment_insights.top_themes.join('；') }}</p>
            <p v-if="breakdown.comment_insights.controversies?.length"><b>争议点：</b>{{ breakdown.comment_insights.controversies.join('；') }}</p>
            <p v-if="breakdown.comment_insights.questions?.length"><b>可回复问题：</b>{{ breakdown.comment_insights.questions.join('；') }}</p>
          </div>

          <div v-if="breakdown.data_context" class="bd-block">
            <h4>数据上下文 <span class="c-meta">（后端确定性计算）</span></h4>
            <div class="bd-metrics">
              <div class="bd-metric"><span class="dim-k">播放</span><span>{{ fmtNum(breakdown.data_context.play) }}</span></div>
              <div class="bd-metric"><span class="dim-k">点赞</span><span>{{ fmtNum(breakdown.data_context.like) }}</span></div>
              <div class="bd-metric"><span class="dim-k">评论</span><span>{{ fmtNum(breakdown.data_context.comment) }}</span></div>
              <div class="bd-metric"><span class="dim-k">播放 / 中位数</span><span>{{ fmtMult(breakdown.data_context.play_vs_median) }}</span></div>
              <div class="bd-metric"><span class="dim-k">点赞 / 中位数</span><span>{{ fmtMult(breakdown.data_context.like_vs_median) }}</span></div>
              <div class="bd-metric"><span class="dim-k">互动率</span><span>{{ fmtPct(breakdown.data_context.engagement_rate) }}</span></div>
              <div class="bd-metric"><span class="dim-k">分位</span><span>{{ fmtPct(breakdown.data_context.percentile, 0) }}</span></div>
            </div>
          </div>

          <div v-if="breakdown.sections?.blueprint?.length" class="bd-block">
            <h4>复刻蓝图</h4>
            <ol class="bd-list"><li v-for="(x, i) in breakdown.sections.blueprint" :key="i">{{ x }}</li></ol>
          </div>

          <div v-if="breakdown.sections?.replication_tips?.length" class="bd-block">
            <h4>落地建议</h4>
            <ul class="bd-list"><li v-for="(x, i) in breakdown.sections.replication_tips" :key="i">{{ x }}</li></ul>
          </div>
        </div>
        <el-empty v-else-if="!isBdActive() && bdStatus !== 'error'" description="尚无拆解报告" :image-size="60" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
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
const proc = reactive<any>({ understand_status: '', use_cover: false, force_category: '' })
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

// 爆款拆解：报告 / 状态 / 轮询。
const breakdown = ref<any>(null)
const bdStatus = ref('')
const bdError = ref('')
const bdBusy = ref(false)
// 本次会话是否已点过「生成/重新拆解」。GET 对「从未生成」也返回 pending，
// 靠它区分「排队中」与「尚未生成」，避免未请求就显示「拆解中」（UX 修正）。
const bdRequested = ref(false)
let bdTimer: number | undefined

// 加工中状态：需轮询等待 worker 完成。
const ACTIVE_STATUS = ['pending', 'doing']
function isProcActive() {
  return ACTIVE_STATUS.includes(proc.understand_status)
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

// 是否在途：本地已请求（pending/doing）或服务端明确 doing 才轮询。
function isBdActive() {
  if (bdStatus.value === 'doing') return true
  return bdRequested.value && bdStatus.value === 'pending'
}
function stopBdPoll() {
  if (bdTimer) { clearInterval(bdTimer); bdTimer = undefined }
}
function syncBdTimer() {
  if (drawer.value && detail.value && isBdActive()) {
    if (!bdTimer) bdTimer = window.setInterval(() => loadBreakdown(detail.value.asset_id), 3000)
  } else {
    stopBdPoll()
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

// 档位取值 -> 中文（两档：basic 普通 / advanced 高级）。
const tierNames: Record<string, string> = { basic: '普通（转录）', advanced: '高级（视频理解）' }
function tierLabel(tier: string | undefined) {
  return tier ? (tierNames[tier] ?? tier) : '未加工'
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
  understanding.value = null
  understandError.value = ''
  const u = await api.get(`/api/assets/${assetId}/understanding`)
  proc.understand_status = u.data.understand_status
  if (detail.value?.status && u.data.process_tier) detail.value.status.tier = u.data.process_tier
  understanding.value = u.data.status === 'pending' ? null : u.data
  understandError.value = u.data.error || ''
  syncProcTimer()
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

// ---- 爆款拆解 ----
const hasCommentInsights = computed(() => {
  const ci = breakdown.value?.comment_insights
  if (!ci) return false
  return ['top_themes', 'controversies', 'questions'].some((k) => ci[k]?.length)
})

async function loadBreakdown(assetId: number) {
  const { data } = await api.get(`/api/assets/${assetId}/breakdown`)
  bdStatus.value = data.status
  if (data.status === 'done') {
    breakdown.value = data
    bdError.value = ''
  } else {
    breakdown.value = null
    bdError.value = data.error || ''
  }
  syncBdTimer()
}

async function runBreakdown(force: boolean) {
  if (!detail.value || bdBusy.value) return
  bdBusy.value = true
  bdRequested.value = true
  try {
    await api.post(`/api/assets/${detail.value.asset_id}/breakdown`, { force })
    ElMessage.success('已加入拆解队列')
    await loadBreakdown(detail.value.asset_id)
  } finally {
    bdBusy.value = false
  }
}

function fmtNum(v: any) {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('zh-CN')
}
function fmtMult(v: any) {
  if (!v) return '—'
  return `${Number(v).toFixed(2)}×`
}
function fmtPct(v: any, digits = 1) {
  if (v === null || v === undefined) return '—'
  return `${(Number(v) * 100).toFixed(digits)}%`
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
  breakdown.value = null
  bdStatus.value = ''
  bdError.value = ''
  bdRequested.value = false
  await loadBreakdown(id)
}

function closeDrawer() {
  stopProcPoll()
  stopBdPoll()
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
  stopBdPoll()
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

/* 爆款拆解 */
.bd-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.bd-report { margin-top: 12px; }
.bd-head { display: flex; gap: 16px; align-items: center; }
.bd-verdict { flex: 1; }
.bd-verdict-text { font-size: 15px; font-weight: 600; margin: 0 0 6px; }
.bd-block { margin-top: 14px; }
.bd-block h4 { margin: 0 0 6px; font-size: 13px; color: #303133; }
.bd-block p { margin: 4px 0; font-size: 13px; }
.bd-metrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 16px; font-size: 13px; }
.bd-metric { display: flex; gap: 8px; }
.bd-list { margin: 4px 0; padding-left: 20px; font-size: 13px; }
.bd-list li { margin: 2px 0; }
</style>
