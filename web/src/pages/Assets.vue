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

    <el-drawer v-model="drawer" title="素材详情" size="50%">
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
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
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
</style>
