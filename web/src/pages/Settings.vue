<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>系统设置</h1>
      <span class="sub">存储目录 · 加工分档 · 类型提示词 · 模型</span>
    </div>

    <!-- 页签头用 V3 样式；el-tabs 只保留内容切换能力（导航条由 CSS 隐藏） -->
    <div class="ui-tabs">
      <button v-for="t in tabs" :key="t.k" class="ui-tab" :class="{ on: tab === t.k }" @click="tab = t.k">
        {{ t.label }}
      </button>
    </div>
    <el-tabs v-model="tab" class="tabs-body">
      <!-- 存储目录 -->
      <el-tab-pane label="存储目录" name="storage">
        <p class="tip">素材保存根目录，支持本机路径、NAS 挂载，Windows 盘符或 UNC（如 Z:\xsai 或 \\NAS\xsai）。</p>
        <div class="bar">
          <button class="ui-btn primary" @click="storageDialog = true">新增根目录</button>
          <span class="grow" />
          <span class="ui-badge">共 {{ roots.length }}</span>
        </div>
        <section class="ui-panel" v-loading="loadingRoots">
          <div v-if="!loadingRoots && !roots.length" class="ui-empty">
            <div class="big">还没有配置存储根目录</div>
            <p>选一个目录作为素材落盘位置，下载与转录都会写到这里。</p>
          </div>
          <table v-else class="ui-table">
            <thead>
              <tr>
                <th style="width: 150px">名称</th>
                <th>路径</th>
                <th style="width: 80px">默认</th>
                <th style="width: 200px">可用性</th>
                <th style="width: 90px; text-align: right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in roots" :key="row.id">
                <td class="strong">{{ row.name }}</td>
                <td class="mono ellip" :title="row.path">{{ row.path }}</td>
                <td><span v-if="row.is_default" class="ui-badge acc">默认</span><span v-else class="muted">—</span></td>
                <td>
                  <span class="ui-badge" :class="row.available ? 'ok' : 'bad'">{{ row.available ? '可用' : '不可用' }}</span>
                  <span class="detail">{{ row.detail }}</span>
                </td>
                <td style="text-align: right">
                  <button class="ui-btn ghost sm danger" @click="delRoot(row)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      </el-tab-pane>

      <!-- 大模型 -->
      <el-tab-pane label="大模型" name="llm">
        <p class="tip">内容分析使用的外部大模型 API（OpenAI 兼容接口）。</p>
        <el-form label-width="120px" style="max-width: 560px">
          <el-form-item label="Base URL">
            <el-input v-model="settings.llm_base_url" placeholder="https://api.openai.com/v1" />
          </el-form-item>
          <el-form-item label="模型名">
            <el-input v-model="settings.llm_model" placeholder="gpt-4o-mini" />
          </el-form-item>
          <el-form-item label="API Key">
            <el-input v-model="settings.llm_api_key" type="password" show-password
              :placeholder="settings.llm_api_key_set ? '已配置（留空不修改）' : '未配置'" />
          </el-form-item>
          <el-form-item label="代理 URL">
            <el-input v-model="settings.proxy_url" placeholder="http://127.0.0.1:7890（可选）" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 评论抓取 -->
      <el-tab-pane label="评论抓取" name="comments">
        <p class="tip">抖音评论富集门控与抓取参数。仅对“够热”的作品抓评论，走 worker 队列，可随时调整。</p>
        <el-form label-width="200px" style="max-width: 640px">
          <el-form-item label="热门分位 top_pct">
            <el-input-number v-model="commentSettings.top_pct" :min="0" :max="1" :step="0.05" :precision="2" />
            <span class="detail">批内按互动分取前该比例的作品（0~1）</span>
          </el-form-item>
          <el-form-item label="单批预算 budget">
            <el-input-number v-model="commentSettings.budget" :min="0" :precision="0" />
            <span class="detail">每批最多入队多少个作品</span>
          </el-form-item>
          <el-form-item label="最低评论数 min_comments">
            <el-input-number v-model="commentSettings.min_comments" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="最低点赞数 min_likes">
            <el-input-number v-model="commentSettings.min_likes" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="评论互动权重 weight">
            <el-input-number v-model="commentSettings.weight" :min="0" :step="1" :precision="1" />
            <span class="detail">互动分 = 点赞 + 评论数 × weight</span>
          </el-form-item>
          <el-form-item label="抓取页数 pages">
            <el-input-number v-model="commentSettings.pages" :min="1" :precision="0" />
          </el-form-item>
          <el-form-item label="每页条数 per_page">
            <el-input-number v-model="commentSettings.per_page" :min="1" :precision="0" />
          </el-form-item>
          <el-form-item label="取 Top-N top_n">
            <el-input-number v-model="commentSettings.top_n" :min="1" :precision="0" />
            <span class="detail">本地按点赞排序后保留的条数</span>
          </el-form-item>
          <el-form-item label="翻页间隔（秒）interval">
            <el-input-number v-model="commentSettings.interval" :min="0" :step="0.5" :precision="2" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveCommentSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 视频理解 -->
      <el-tab-pane label="视频理解" name="process">
        <p class="tip">素材分档加工门控（普通=转录 / 高级=视频理解）与类型提示词。阈值存 system_setting，可在线调整，不写死。</p>
        <el-form label-width="210px" style="max-width: 680px">
          <el-form-item label="高级档热度阈值 min_heat">
            <el-input-number v-model="proc.min_heat" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="最低点赞下限 min_likes_floor">
            <el-input-number v-model="proc.min_likes_floor" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="单批预算 budget">
            <el-input-number v-model="proc.budget" :min="0" :precision="0" />
            <span class="detail">全局限额：高级档在途 job（含 queued/running）上限；单条下载路径也受约束</span>
          </el-form-item>
          <el-form-item label="热门分位 top_pct">
            <el-input-number v-model="proc.top_pct" :min="0" :max="1" :step="0.05" :precision="2" />
            <span class="detail">仅批量路径（采集/同步）生效：每批按热度取前该比例升档；单条下载路径不适用</span>
          </el-form-item>
          <el-form-item label="点赞权重 weight_like">
            <el-input-number v-model="proc.weight_like" :min="0" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="评论权重 weight_comment">
            <el-input-number v-model="proc.weight_comment" :min="0" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="播放权重 weight_play">
            <el-input-number v-model="proc.weight_play" :min="0" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="视频内联上限 video_max_mb">
            <el-input-number v-model="proc.video_max_mb" :min="0" :step="1" :precision="2" />
          </el-form-item>
          <el-form-item label="封面内联上限 cover_max_mb">
            <el-input-number v-model="proc.cover_max_mb" :min="0" :step="1" :precision="2" />
          </el-form-item>
          <el-form-item label="低置信度阈值">
            <el-input-number v-model="proc.low_confidence_threshold" :min="0" :max="1" :step="0.05" :precision="2" />
          </el-form-item>
          <el-form-item label="开启判类 classify_enabled">
            <el-switch v-model="proc.classify_enabled" />
          </el-form-item>
          <el-form-item label="判类默认用封面 use_cover">
            <el-switch v-model="proc.use_cover" />
          </el-form-item>
          <el-form-item label="视频理解 provider">
            <el-select v-model="proc.provider" style="width: 200px">
              <el-option label="qwen（OpenAI 兼容）" value="qwen" />
              <el-option label="gemini（原生）" value="gemini" />
            </el-select>
          </el-form-item>
          <el-form-item label="视觉模型 vision_model">
            <el-input v-model="proc.vision_model" placeholder="qwen3.8-flash" />
          </el-form-item>
          <el-form-item label="判类模型 classify_model">
            <el-input v-model="proc.classify_model" placeholder="qwen3.8-flash" />
          </el-form-item>
          <el-form-item label="类型目录 taxonomy（JSON）">
            <el-input v-model="proc.taxonomyText" type="textarea" :rows="6" spellcheck="false" />
            <span class="detail">枚举 → 中文，可增删类型</span>
          </el-form-item>
          <el-form-item label="类型提示词 type_prompts（JSON）">
            <el-input v-model="proc.typePromptsText" type="textarea" :rows="10" spellcheck="false" />
            <span class="detail">类型 → {focus, dimensions:[{key,name,ask}]}，保存前本地校验</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveProcessSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 爆款拆解 -->
      <el-tab-pane label="爆款拆解" name="breakdown">
        <p class="tip">爆款拆解报告使用的提示词，以及「对标账号」里判定爆款的热度倍数（播放 ≥ 该账号自身中位数 × 倍数）。</p>
        <el-form label-width="140px" style="max-width: 720px">
          <el-form-item label="拆解提示词">
            <el-input v-model="bd.prompt" type="textarea" :rows="14" />
          </el-form-item>
          <el-form-item label="爆款倍数 viral_multiple">
            <el-input-number v-model="bd.viral_multiple" :min="0.1" :step="0.5" :precision="1" />
            <span class="detail">必须为正数</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveBreakdownSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 诊断 -->
      <el-tab-pane label="依赖诊断" name="diag">
        <el-button @click="loadDiag">重新检测</el-button>
        <el-table :data="checks" v-loading="loadingDiag" stripe style="margin-top: 12px">
          <el-table-column label="检查项" width="160">
            <template #default="{ row }">{{ row.name }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.ok ? 'success' : 'danger'" size="small">{{ row.ok ? 'OK' : '异常' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="280">
            <template #default="{ row }">{{ row.detail }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 管理员 -->
      <el-tab-pane label="管理员" name="admin">
        <el-form label-width="120px" style="max-width: 480px">
          <el-form-item label="原密码">
            <el-input v-model="pwd.old_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="新密码">
            <el-input v-model="pwd.new_password" type="password" show-password placeholder="至少 6 位" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="changePwd">修改密码</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <!-- 删除存储根确认 -->
    <div v-if="rootTarget" class="ui-modal" @click.self="rootTarget = null">
      <div class="ui-modal-box">
        <div class="ui-panel-head"><h2>删除存储根目录</h2></div>
        <div class="ui-modal-body">
          <p>确定删除「{{ rootTarget.name }}」({{ rootTarget.path }})？已入库素材的路径会失效。</p>
        </div>
        <div class="ui-modal-foot">
          <button class="ui-btn" @click="rootTarget = null">取消</button>
          <button class="ui-btn danger" @click="doDelRoot">确认删除</button>
        </div>
      </div>
    </div>

    <!-- 新增存储根弹窗 -->
    <el-dialog v-model="storageDialog" title="新增存储根目录" width="480px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="rootForm.name" placeholder="例如：本机素材 / NAS" />
        </el-form-item>
        <el-form-item label="路径">
          <el-input v-model="rootForm.path" placeholder="/mnt/nas/xsai-content" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="rootForm.is_default" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="storageDialog = false">取消</el-button>
        <el-button type="primary" @click="createRoot">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const tab = ref('storage')
// 页签清单：与下面 el-tab-pane 的 name 一一对应
const tabs = [
  { k: 'storage', label: '存储目录' },
  { k: 'llm', label: '大模型' },
  { k: 'comments', label: '评论抓取' },
  { k: 'process', label: '视频理解' },
  { k: 'breakdown', label: '爆款拆解' },
  { k: 'diag', label: '依赖诊断' },
  { k: 'admin', label: '管理员' },
]
const roots = ref<any[]>([])
const checks = ref<any[]>([])
const loadingRoots = ref(false)
const loadingDiag = ref(false)
const storageDialog = ref(false)
const rootForm = reactive({ name: '', path: '', is_default: false })
const settings = reactive<any>({ llm_base_url: '', llm_model: '', llm_api_key: '', proxy_url: '', llm_api_key_set: false })
const pwd = reactive({ old_password: '', new_password: '' })
const commentSettings = reactive({
  top_pct: 0.2,
  budget: 10,
  min_comments: 20,
  min_likes: 100,
  weight: 20,
  pages: 2,
  per_page: 20,
  top_n: 20,
  interval: 1,
})
const proc = reactive<any>({
  min_heat: 1000,
  min_likes_floor: 100,
  budget: 20,
  top_pct: 0.2,
  weight_like: 1,
  weight_comment: 2,
  weight_play: 0,
  video_max_mb: 15,
  cover_max_mb: 5,
  low_confidence_threshold: 0.6,
  classify_enabled: true,
  use_cover: false,
  provider: 'qwen',
  vision_model: 'qwen3.8-flash',
  classify_model: 'qwen3.8-flash',
  taxonomyText: '{}',
  typePromptsText: '{}',
})

const bd = reactive<any>({ prompt: '', viral_multiple: 3 })

async function loadBreakdownSettings() {
  const { data } = await api.get('/api/system/breakdown-settings')
  bd.prompt = data.prompt ?? ''
  bd.viral_multiple = data.viral_multiple ?? 3
}

async function saveBreakdownSettings() {
  if (!bd.prompt || !bd.prompt.trim()) return ElMessage.warning('提示词不能为空')
  if (!(bd.viral_multiple > 0)) return ElMessage.warning('爆款倍数必须为正数')
  await api.put('/api/system/breakdown-settings', {
    prompt: bd.prompt,
    viral_multiple: bd.viral_multiple,
  })
  ElMessage.success('已保存')
  await loadBreakdownSettings()
}

async function loadRoots() {
  loadingRoots.value = true
  try {
    roots.value = (await api.get('/api/system/storage-roots')).data
  } finally {
    loadingRoots.value = false
  }
}

async function createRoot() {
  if (!rootForm.name.trim() || !rootForm.path.trim()) return ElMessage.warning('名称和路径必填')
  await api.post('/api/system/storage-roots', rootForm)
  storageDialog.value = false
  rootForm.name = ''
  rootForm.path = ''
  rootForm.is_default = false
  await loadRoots()
}

const rootTarget = ref<any>(null)

function delRoot(row: any) {
  rootTarget.value = row
}

async function doDelRoot() {
  if (!rootTarget.value) return
  await api.delete(`/api/system/storage-roots/${rootTarget.value.id}`)
  rootTarget.value = null
  await loadRoots()
}

async function loadSettings() {
  const { data } = await api.get('/api/system/settings')
  Object.assign(settings, data, { llm_api_key: '' })
}

async function saveSettings() {
  await api.put('/api/system/settings', {
    llm_base_url: settings.llm_base_url,
    llm_model: settings.llm_model,
    llm_api_key: settings.llm_api_key,
    proxy_url: settings.proxy_url,
  })
  ElMessage.success('已保存')
  settings.llm_api_key = ''
  await loadSettings()
}

async function loadDiag() {
  loadingDiag.value = true
  try {
    checks.value = (await api.get('/api/system/diagnostics')).data.checks
  } finally {
    loadingDiag.value = false
  }
}

async function changePwd() {
  if (pwd.new_password.length < 6) return ElMessage.warning('新密码至少 6 位')
  await api.post('/api/auth/change-password', pwd)
  ElMessage.success('密码已修改')
  pwd.old_password = ''
  pwd.new_password = ''
}

async function loadCommentSettings() {
  Object.assign(commentSettings, (await api.get('/api/system/comment-settings')).data)
}

async function saveCommentSettings() {
  await api.put('/api/system/comment-settings', commentSettings)
  ElMessage.success('已保存')
}

async function loadProcessSettings() {
  const { data } = await api.get('/api/system/process-settings')
  Object.assign(proc, data, {
    taxonomyText: JSON.stringify(data.taxonomy ?? {}, null, 2),
    typePromptsText: JSON.stringify(data.type_prompts ?? {}, null, 2),
  })
}

async function saveProcessSettings() {
  let taxonomy: any
  let type_prompts: any
  try {
    taxonomy = JSON.parse(proc.taxonomyText)
    type_prompts = JSON.parse(proc.typePromptsText)
  } catch (e: any) {
    return ElMessage.error('JSON 解析失败：' + e.message)
  }
  await api.put('/api/system/process-settings', {
    min_heat: proc.min_heat,
    min_likes_floor: proc.min_likes_floor,
    budget: proc.budget,
    top_pct: proc.top_pct,
    weight_like: proc.weight_like,
    weight_comment: proc.weight_comment,
    weight_play: proc.weight_play,
    video_max_mb: proc.video_max_mb,
    cover_max_mb: proc.cover_max_mb,
    low_confidence_threshold: proc.low_confidence_threshold,
    classify_enabled: proc.classify_enabled,
    use_cover: proc.use_cover,
    provider: proc.provider,
    vision_model: proc.vision_model,
    classify_model: proc.classify_model,
    taxonomy,
    type_prompts,
  })
  ElMessage.success('已保存')
  await loadProcessSettings()
}

onMounted(() => {
  loadRoots()
  loadSettings()
  loadDiag()
  loadCommentSettings()
  loadProcessSettings()
  loadBreakdownSettings()
})
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; margin-bottom: var(--sp-3); }
.bar .grow { margin-left: auto; }
.mono { font-family: var(--font-mono); font-size: var(--fs-sm); }
.ellip { max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.muted { color: var(--muted); }

/* 隐藏 el-tabs 自带的导航条，只保留内容切换能力 */
.tabs-body :deep(.el-tabs__header) { display: none; }

/* 删除确认弹层 */
.ui-modal {
  position: fixed; inset: 0; z-index: 200;
  background: color-mix(in oklab, var(--ink) 45%, transparent);
  display: grid; place-items: center; padding: var(--sp-5);
}
.ui-modal-box { background: var(--surface); border: 2px solid var(--ink); box-shadow: var(--shadow-2); width: min(520px, 100%); }
.ui-modal-body { padding: var(--sp-5); color: var(--ink-2); }
.ui-modal-body p { margin: 0; word-break: break-all; }
.ui-modal-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: var(--sp-4) var(--sp-5); border-top: 1px solid var(--line-soft);
}
.tip {
  color: var(--muted);
  font-size: var(--fs-sm);
  margin: 4px 0 12px;
  line-height: 1.6;
}
.detail {
  color: #909399;
  font-size: 12px;
  margin-left: 8px;
}
</style>
