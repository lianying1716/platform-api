<template>
  <div>
    <el-tabs v-model="tab">
      <!-- 存储目录 -->
      <el-tab-pane label="存储目录" name="storage">
        <p class="tip">素材保存根目录，支持本机路径、NAS 挂载，Windows 盘符或 UNC（如 Z:\\xsai 或 \\\\NAS\\xsai）。</p>
        <el-button type="primary" @click="storageDialog = true">新增根目录</el-button>
        <el-table :data="roots" v-loading="loadingRoots" stripe style="margin-top: 12px">
          <el-table-column prop="name" label="名称" width="140" />
          <el-table-column prop="path" label="路径" min-width="220" show-overflow-tooltip />
          <el-table-column label="默认" width="80">
            <template #default="{ row }">
              <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="可用性" width="200">
            <template #default="{ row }">
              <el-tag :type="row.available ? 'success' : 'danger'" size="small">
                {{ row.available ? '可用' : '不可用' }}
              </el-tag>
              <span class="detail">{{ row.detail }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-popconfirm title="删除该根目录？已入库素材路径会失效" @confirm="delRoot(row)">
                <template #reference>
                  <el-button size="small" type="danger" plain>删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
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

async function delRoot(row: any) {
  await api.delete(`/api/system/storage-roots/${row.id}`)
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
})
</script>

<style scoped>
.tip {
  color: #909399;
  font-size: 13px;
  margin: 4px 0 12px;
}
.detail {
  color: #909399;
  font-size: 12px;
  margin-left: 8px;
}
</style>
