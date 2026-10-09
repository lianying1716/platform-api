<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>账号中心</h1>
      <span class="sub">登录凭证、状态体检与扫码登录</span>
    </div>

    <div class="toolbar">
      <button class="ui-btn primary" @click="openCreate">新建账号</button>
      <button class="ui-btn" :disabled="loading" @click="load">刷新</button>
      <span class="grow" />
      <span class="ui-badge" :class="{ ok: stats.active }">在线 {{ stats.active }}</span>
      <span class="ui-badge" :class="{ bad: stats.down }">掉线/异常 {{ stats.down }}</span>
    </div>

    <div v-loading="loading">
      <div v-if="!loading && !accounts.length" class="ui-empty">
        <div class="big">还没有接入账号</div>
        <p>点「新建账号」用扫码或 Cookie 接入抖音 / B站等平台，之后即可同步关注列表与素材。</p>
      </div>
      <div v-else class="acc-grid">
        <article v-for="row in accounts" :key="row.id" class="acc">
          <div class="acc-top">
            <PlatformMark :platform="row.platform" size="md" />
            <span class="ui-badge" :class="statusTone(row.status)">
              <span class="ui-dot" :class="statusTone(row.status)" />
              {{ statusText(row.status) }}
            </span>
          </div>
          <div class="acc-name">{{ row.display_name || row.external_id || '未命名账号' }}</div>
          <p class="acc-id" :title="row.external_id">
            {{ row.platform_label }} · ID {{ row.external_id ? row.external_id.slice(-8) : '待识别' }}
          </p>
          <dl class="acc-meta">
            <div><dt>到期</dt><dd>{{ fmt(row.expires_at) }}</dd></div>
            <div><dt>上次体检</dt><dd>{{ fmt(row.last_checked_at) }}</dd></div>
          </dl>
          <p v-if="row.status_message" class="acc-msg" :title="row.status_message">{{ row.status_message }}</p>
          <div class="acc-acts">
            <button class="ui-btn sm" :disabled="checking === row.id" @click="check(row)">
              {{ checking === row.id ? '体检中…' : '体检' }}
            </button>
            <button class="ui-btn sm" @click="openUpdate(row)">更新凭证</button>
            <button class="ui-btn sm danger" @click="confirmRemove(row)">删除</button>
          </div>
        </article>
      </div>
    </div>

    <!-- 删除确认（不用 el-popconfirm，保持与卡片一致的硬边风格） -->
    <div v-if="removeTarget" class="ui-modal" @click.self="removeTarget = null">
      <div class="ui-modal-box narrow">
        <div class="ui-panel-head"><h2>删除账号</h2></div>
        <div class="ui-modal-body">
          <p>确定删除「{{ removeTarget.display_name || removeTarget.platform_label }}」？账号将停用，已采集的素材会保留。</p>
        </div>
        <div class="ui-modal-foot">
          <button class="ui-btn" @click="removeTarget = null">取消</button>
          <button class="ui-btn danger" @click="doRemove">确认删除</button>
        </div>
      </div>
    </div>

    <!-- 新建/更新账号弹窗 -->
    <el-dialog v-model="dialog" :title="editing ? '更新凭证' : '新建账号'" width="520px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="平台" v-if="!editing">
          <!-- 平台卡片选择器：图标 + 名称 + 能力标签，比纯下拉更容易分辨 -->
          <div class="plat-picker">
            <button
              v-for="p in platforms"
              :key="p.platform"
              type="button"
              class="plat-opt"
              :class="{ on: form.platform === p.platform }"
              @click="form.platform = p.platform; onPlatformChange()"
            >
              <PlatformMark :platform="p.platform" :show-label="false" size="md" />
              <span class="po-name">{{ p.label }}</span>
              <span v-if="p.supports_qr" class="po-tag">扫码</span>
            </button>
          </div>
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
        <p v-if="qrStatus === 'expired' || qrStatus === 'error'" class="qr-hint">
          二维码已失效，点下方按钮重新获取
        </p>
        <button
          v-if="qrStatus === 'expired' || qrStatus === 'error'"
          class="ui-btn primary sm"
          :disabled="submitting"
          @click="startQr"
        >
          重新获取二维码
        </button>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { InfoFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import PlatformMark from '../components/PlatformMark.vue'
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
let pollErrors = 0

const form = reactive<{ platform: string; display_name: string; credential: Record<string, string> }>({
  platform: 'bilibili',
  display_name: '',
  credential: {},
})

const currentPlatform = computed(() => platforms.value.find((p) => p.platform === form.platform))

const stats = computed(() => ({
  active: accounts.value.filter((a) => a.status === 'active').length,
  down: accounts.value.filter((a) => a.status === 'expired' || a.status === 'error').length,
}))

// 状态 → V3 徽标/圆点色调（active / expired / error / unchecked）
function statusTone(s: string) {
  return ({ active: 'ok', expired: 'bad', error: 'warn' } as Record<string, string>)[s] ?? ''
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
    pollErrors = 0
    pollTimer = window.setInterval(() => poll(data.session_id), 3000)
  } finally {
    submitting.value = false
  }
}

async function poll(sessionId: string) {
  // 连续失败才停：偶发一次网络抖动不该中断整个扫码流程
  try {
    const { data } = await api.get(`/api/accounts/qr-login/${sessionId}`)
    pollErrors = 0
    qrMessage.value = data.message
    qrStatus.value = data.status
    if (data.status === 'success') {
      stopPolling()
      qrDialog.value = false
      ElMessage.success('扫码登录成功')
      await load()
    } else if (data.status === 'expired' || data.status === 'error') {
      stopPolling()
      if (data.status === 'expired') {
        ElMessage.warning('二维码已超时，请重新获取')
      }
    }
  } catch (e: any) {
    pollErrors += 1
    if (pollErrors >= 3) {
      stopPolling()
      qrStatus.value = 'error'
      qrMessage.value = e?.response?.data?.detail || '轮询失败，请关闭后重新获取二维码'
    }
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

const removeTarget = ref<Account | null>(null)

function confirmRemove(row: Account) {
  removeTarget.value = row
}

async function doRemove() {
  if (!removeTarget.value) return
  await api.delete(`/api/accounts/${removeTarget.value.id}`)
  removeTarget.value = null
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
  gap: var(--sp-3);
  align-items: center;
  flex-wrap: wrap;
}
.toolbar .grow { margin-left: auto; }

/* 账号卡片 */
.acc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--sp-3);
}
.acc {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: var(--sp-4);
  background: var(--surface);
  border: 1px solid var(--ink);
  box-shadow: var(--shadow-1);
  transition: transform .14s ease-out, box-shadow .14s ease-out;
}
.acc:hover { transform: translate(-1px, -1px); box-shadow: var(--shadow-2); }

/* 平台卡片选择器 */
.plat-picker {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(158px, 1fr));
  gap: 8px;
  width: 100%;
}
.plat-opt {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  background: var(--surface);
  border: 2px solid var(--line-soft);
  box-shadow: none;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: var(--ink);
  min-width: 0;
}
.plat-opt:hover { border-color: var(--ink-2); }
.plat-opt.on {
  border-color: var(--accent);
  box-shadow: 3px 3px 0 var(--accent);
}
.po-name {
  font-weight: 700;
  font-size: var(--fs-sm);
  white-space: nowrap;
}
.po-tag {
  position: absolute;
  top: -1px;
  right: -1px;
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  padding: 2px 4px;
  background: var(--accent-soft);
  color: var(--accent);
  border-left: 1px solid var(--accent-line);
  border-bottom: 1px solid var(--accent-line);
}
.acc-top { display: flex; align-items: center; gap: 8px; }
.acc-top .ui-badge { margin-left: auto; }
.acc-name {
  font-size: var(--fs-lg);
  font-weight: 700;
  color: var(--ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.acc-meta { display: flex; flex-direction: column; gap: 3px; margin: 0; }
.acc-meta > div { display: flex; gap: 8px; font-size: var(--fs-xs); }
.acc-meta dt { color: var(--muted); min-width: 56px; margin: 0; }
.acc-meta dd { color: var(--ink-2); margin: 0; font-variant-numeric: tabular-nums; }
.acc-msg {
  margin: 0; font-size: var(--fs-xs); color: var(--muted);
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden;
}
.acc-acts { display: flex; gap: 6px; margin-top: auto; padding-top: 8px; border-top: 1px solid var(--line-soft); flex-wrap: wrap; }
.ui-badge .ui-dot { margin-right: 2px; }

/* 确认弹层（与全局 ui-modal 一致，宽度收窄） */
.ui-modal {
  position: fixed; inset: 0; z-index: 200;
  background: color-mix(in oklab, var(--ink) 45%, transparent);
  display: grid; place-items: center; padding: var(--sp-5);
}
.ui-modal-box {
  background: var(--surface); border: 2px solid var(--ink); box-shadow: var(--shadow-2);
  width: min(480px, 100%);
}
.ui-modal-body { padding: var(--sp-5); color: var(--ink-2); }
.ui-modal-body p { margin: 0; }
.ui-modal-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: var(--sp-4) var(--sp-5); border-top: 1px solid var(--line-soft);
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
  color: var(--muted);
}

.qr-status.scanned {
  color: var(--warn);
  font-weight: 700;
}

.qr-hint {
  font-size: 13px;
  color: var(--muted);
  margin: 4px 0 10px;
}

.qr-status.success {
  color: var(--ok);
}

.qr-status.expired,
.qr-status.error {
  color: var(--danger);
}
</style>
