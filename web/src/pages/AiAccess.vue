<template>
  <div>
    <el-tabs v-model="tab">
      <!-- API Key 管理 -->
      <el-tab-pane label="API Key" name="keys">
        <el-button type="primary" @click="dialog = true">创建 Key</el-button>
        <el-table :data="keys" v-loading="loading" stripe style="margin-top: 12px">
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column prop="prefix" label="前缀" width="120" />
          <el-table-column label="权限" min-width="160">
            <template #default="{ row }">
              <el-tag v-for="s in row.scopes" :key="s" size="small" style="margin-right: 4px">{{ s }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="rate_limit_per_min" label="限流/分" width="100" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
                {{ row.enabled ? '启用' : '已吊销' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-popconfirm title="吊销后立即失效，确定？" @confirm="revoke(row)">
                <template #reference>
                  <el-button size="small" type="danger" plain :disabled="!row.enabled">吊销</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- MCP 接入 -->
      <el-tab-pane label="MCP 接入" name="mcp">
        <p class="tip">把下面的配置加到 Claude Code / Codex 的 MCP 配置里，即可让 AI 调用平台能力。</p>
        <el-input type="textarea" :rows="12" :model-value="mcpConfig" readonly />
      </el-tab-pane>

      <!-- 调用日志 -->
      <el-tab-pane label="调用日志" name="logs">
        <el-button @click="loadLogs">刷新</el-button>
        <el-table :data="logs" v-loading="loadingLogs" stripe style="margin-top: 12px">
          <el-table-column prop="created_at" label="时间" width="190">
            <template #default="{ row }">{{ fmt(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="key_id" label="Key" width="80" />
          <el-table-column prop="tool" label="工具" min-width="160" />
          <el-table-column prop="params_summary" label="参数" min-width="180" show-overflow-tooltip />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="duration_ms" label="耗时(ms)" width="100" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 创建 Key 弹窗 -->
    <el-dialog v-model="dialog" title="创建 API Key" width="460px">
      <el-form label-width="90px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="例如：我的智能体" />
        </el-form-item>
        <el-form-item label="权限范围">
          <el-checkbox-group v-model="form.scopes">
            <el-checkbox value="read">read（查询）</el-checkbox>
            <el-checkbox value="operate">operate（建任务/登录）</el-checkbox>
            <el-checkbox value="admin">admin（设置）</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="限流/分">
          <el-input-number v-model="form.rate_limit_per_min" :min="1" :max="6000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <!-- 新 Key 明文展示（仅一次） -->
    <el-dialog v-model="showKey" title="请立即保存（只显示这一次）" width="520px">
      <el-alert type="warning" :closable="false" title="关闭后无法再次查看此 Key 明文" style="margin-bottom: 12px" />
      <el-input :model-value="newKey" readonly>
        <template #append>
          <el-button @click="copyKey">复制</el-button>
        </template>
      </el-input>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const tab = ref('keys')
const keys = ref<any[]>([])
const logs = ref<any[]>([])
const loading = ref(false)
const loadingLogs = ref(false)
const dialog = ref(false)
const showKey = ref(false)
const newKey = ref('')
const form = reactive({ name: '', scopes: ['read', 'operate'], rate_limit_per_min: 60 })

const mcpConfig = computed(() =>
  JSON.stringify(
    {
      mcpServers: {
        'xsai-workbench': {
          url: `${location.origin}/api/skills`,
          headers: { Authorization: 'Bearer <在此粘贴你创建的 API Key>' },
        },
      },
    },
    null,
    2,
  ),
)

function fmt(v: string | null) {
  return v ? new Date(v.endsWith('Z') ? v : v + 'Z').toLocaleString('zh-CN', { hour12: false }) : '—'
}

async function load() {
  loading.value = true
  try {
    keys.value = (await api.get('/api/ai/keys')).data
  } finally {
    loading.value = false
  }
}

async function loadLogs() {
  loadingLogs.value = true
  try {
    logs.value = (await api.get('/api/ai/call-logs')).data
  } finally {
    loadingLogs.value = false
  }
}

async function create() {
  if (!form.name.trim()) return ElMessage.warning('请填名称')
  if (!form.scopes.length) return ElMessage.warning('至少选一个权限')
  const { data } = await api.post('/api/ai/keys', form)
  dialog.value = false
  newKey.value = data.api_key
  showKey.value = true
  form.name = ''
  await load()
}

function copyKey() {
  navigator.clipboard?.writeText(newKey.value)
  ElMessage.success('已复制')
}

async function revoke(row: any) {
  await api.delete(`/api/ai/keys/${row.id}`)
  ElMessage.success('已吊销')
  await load()
}

onMounted(load)
</script>

<style scoped>
.tip {
  color: #909399;
  font-size: 13px;
  margin: 4px 0 12px;
}
</style>
