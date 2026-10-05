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

onMounted(() => {
  loadRoots()
  loadSettings()
  loadDiag()
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
