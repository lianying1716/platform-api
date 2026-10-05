<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建账号</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table :data="accounts" v-loading="loading" style="margin-top: 14px">
      <el-table-column label="平台" width="110">
        <template #default="{ row }">{{ row.platform_label }}</template>
      </el-table-column>
      <el-table-column prop="display_name" label="备注/昵称" min-width="120">
        <template #default="{ row }">{{ row.display_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="160">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" effect="dark" size="small">{{ statusText(row.status) }}</el-tag>
          <el-tooltip v-if="row.status_message" :content="row.status_message">
            <el-icon style="margin-left: 6px; color: #909399">
              <InfoFilled />
            </el-icon>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="到期时间" width="170">
        <template #default="{ row }">{{ fmt(row.expires_at) }}</template>
      </el-table-column>
      <el-table-column label="上次体检" width="170">
        <template #default="{ row }">{{ fmt(row.last_checked_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :loading="checking === row.id" @click="check(row)">体检</el-button>
          <el-button size="small" @click="openUpdate(row)">更新凭证</el-button>
          <el-popconfirm title="确定删除这个账号？凭证将一并删除" @confirm="remove(row)">
            <template #reference>
              <el-button size="small" type="danger" plain>删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/更新账号弹窗 -->
    <el-dialog v-model="dialog" :title="editing ? '更新凭证' : '新建账号'" width="520px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="平台" v-if="!editing">
          <el-select v-model="form.platform" @change="onPlatformChange">
            <el-option v-for="p in platforms" :key="p.platform" :label="p.label" :value="p.platform" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注" v-if="!editing">
          <el-input v-model="form.display_name" placeholder="例如：主号 / 运营号（可留空，自动取昵称）" />
        </el-form-item>
        <el-form-item label="录入方式" v-if="currentPlatform?.supports_qr">
          <el-radio-group v-model="mode">
            <el-radio-button value="qr">扫码登录</el-radio-button>
            <el-radio-button value="manual">手动粘 Cookie</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="mode === 'manual'">
          <el-form-item v-for="f in currentPlatform?.credential_fields" :key="f.key"
            :label="f.required ? '* ' + '凭证' : '凭证'" label-width="60px">
            <el-input v-model="form.credential[f.key]" :placeholder="f.label"
              :type="f.key === 'cookie' ? 'textarea' : 'text'" :rows="3" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">
          {{ mode === 'qr' ? '获取二维码' : '保存并体检' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 扫码弹窗 -->
    <el-dialog v-model="qrDialog" title="扫码登录" width="360px" @closed="stopPolling" destroy-on-close>
      <div class="qr-box">
        <img v-if="qrImage" :src="`data:image/png;base64,${qrImage}`" alt="二维码" class="qr-img" />
        <p class="qr-status" :class="qrStatus">{{ qrMessage }}</p>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { InfoFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api, type Account, type PlatformInfo } from '../api'

const accounts = ref<Account[]>([])
const platforms = ref<PlatformInfo[]>([])
const loading = ref(false)
const checking = ref<number | null>(null)
const dialog = ref(false)
const submitting = ref(false)
const editing = ref<Account | null>(null)
const mode = ref<'qr' | 'manual'>('qr')

const qrDialog = ref(false)
const qrImage = ref('')
const qrMessage = ref('')
const qrStatus = ref('pending')
let pollTimer: number | undefined

const form = reactive<{ platform: string; display_name: string; credential: Record<string, string> }>({
  platform: 'bilibili',
  display_name: '',
  credential: {},
})

const currentPlatform = computed(() => platforms.value.find((p) => p.platform === form.platform))

function statusType(s: string) {
  return ({ active: 'success', expired: 'danger', error: 'warning' } as Record<string, string>)[s] ?? 'info'
}
function statusText(s: string) {
  return ({ active: '在线', expired: '已掉线', error: '异常', unchecked: '未体检' } as Record<string, string>)[s] ?? s
}
function fmt(v: string | null) {
  if (!v) return '—'
  return new Date(v.endsWith('Z') ? v : v + 'Z').toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/api/accounts')
    accounts.value = data
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.platform = 'bilibili'
  form.display_name = ''
  form.credential = {}
  mode.value = currentPlatform.value?.supports_qr ? 'qr' : 'manual'
  dialog.value = true
}

function openUpdate(row: Account) {
  editing.value = row
  form.platform = row.platform
  form.credential = {}
  mode.value = 'manual'
  dialog.value = true
}

function onPlatformChange() {
  form.credential = {}
  mode.value = currentPlatform.value?.supports_qr ? 'qr' : 'manual'
}

async function submit() {
  if (mode.value === 'qr') {
    await startQr()
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      await api.put(`/api/accounts/${editing.value.id}/credential`, { credential: form.credential })
      ElMessage.success('凭证已更新')
    } else {
      await api.post('/api/accounts', {
        platform: form.platform,
        display_name: form.display_name,
        credential: form.credential,
      })
      ElMessage.success('账号已创建')
    }
    dialog.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

async function startQr() {
  submitting.value = true
  try {
    const { data } = await api.post('/api/accounts/qr-login', {
      platform: form.platform,
      display_name: form.display_name,
    })
    dialog.value = false
    qrImage.value = data.qr_image_base64
    qrMessage.value = '请用手机 App 扫码'
    qrStatus.value = 'pending'
    qrDialog.value = true
    pollTimer = window.setInterval(() => poll(data.session_id), 3000)
  } finally {
    submitting.value = false
  }
}

async function poll(sessionId: string) {
  try {
    const { data } = await api.get(`/api/accounts/qr-login/${sessionId}`)
    qrMessage.value = data.message
    qrStatus.value = data.status
    if (data.status === 'success') {
      stopPolling()
      qrDialog.value = false
      ElMessage.success('扫码登录成功')
      await load()
    } else if (data.status === 'expired' || data.status === 'error') {
      stopPolling()
    }
  } catch {
    stopPolling()
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = undefined
  }
}

async function check(row: Account) {
  checking.value = row.id
  try {
    const { data } = await api.post(`/api/accounts/${row.id}/check`)
    ElMessage({ message: data.message || data.status, type: data.ok ? 'success' : 'warning' })
    await load()
  } finally {
    checking.value = null
  }
}

async function remove(row: Account) {
  await api.delete(`/api/accounts/${row.id}`)
  ElMessage.success('已删除')
  await load()
}

onMounted(async () => {
  const { data } = await api.get('/api/platforms')
  platforms.value = data
  await load()
})
onUnmounted(stopPolling)
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
}

.qr-box {
  text-align: center;
}

.qr-img {
  width: 240px;
  height: 240px;
  object-fit: contain;
}

.qr-status {
  font-size: 14px;
  color: #606266;
}

.qr-status.scanned {
  color: #e6a23c;
}

.qr-status.success {
  color: #67c23a;
}

.qr-status.expired,
.qr-status.error {
  color: #f56c6c;
}
</style>
