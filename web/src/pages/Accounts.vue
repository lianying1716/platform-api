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
            <span v-if="!row.sync_enabled" class="ui-badge warn">沉默期</span>
          </div>
          <div class="acc-name">{{ row.display_name || row.external_id || '未命名账号' }}</div>
          <p class="acc-id" :title="row.external_id">
            {{ row.platform_label }} · ID {{ row.external_id ? row.external_id.slice(-8) : '待识别' }}
          </p>
          <!-- 同步总开关：关闭 = 沉默期，该账号所有同步/采集/体检一律禁止 -->
          <div class="acc-sync" :class="{ off: !row.sync_enabled }">
            <div class="acc-sync-text">
              <span class="acc-sync-title">同步</span>
              <span class="acc-sync-sub">
                {{ row.sync_enabled ? '已开启 · 按配置正常同步' : '沉默期 · 该账号所有同步/调用已禁止' }}
              </span>
            </div>
            <div class="grow" />
            <button
              class="ui-switch"
              :class="{ on: row.sync_enabled }"
              :disabled="togglingSync === row.id"
              :aria-label="row.sync_enabled ? '关闭该账号同步（进入沉默期）' : '开启该账号同步'"
              @click="toggleSync(row)"
            >
              <i />
            </button>
          </div>
          <dl class="acc-meta">
            <div><dt>到期</dt><dd>{{ fmt(row.expires_at) }}</dd></div>
            <div><dt>上次体检</dt><dd>{{ fmt(row.last_checked_at) }}</dd></div>
            <div>
              <dt>存储位置</dt>
              <dd class="storage-cell" :title="row.storage_root_path || '默认目录'">
                <span v-if="row.storage_root_name" class="ui-badge acc">{{ row.storage_root_name }}</span>
                <span v-else class="ui-badge">默认目录</span>
                <span class="storage-path mono ellip">{{ row.storage_root_path || '未配置根目录' }}</span>
              </dd>
            </div>
          </dl>
          <p v-if="row.status_message" class="acc-msg" :title="row.status_message">{{ row.status_message }}</p>
          <div class="acc-acts">
            <button class="ui-btn sm" :disabled="checking === row.id" @click="check(row)">
              {{ checking === row.id ? '体检中…' : '体检' }}
            </button>
            <button class="ui-btn sm" @click="openUpdate(row)">更新凭证</button>
            <button class="ui-btn sm" @click="openStorage(row)">设置目录</button>
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

    <!-- 设置存储目录弹窗 -->
    <el-dialog v-model="storageDialog" title="设置存储目录" width="520px" destroy-on-close>
      <p class="storage-tip">
        每个账号可以单独指定一个根目录，也可以共用同一个。留空则使用系统默认根目录。
      </p>
      <el-form label-width="90px">
        <el-form-item label="根目录">
          <el-select v-model="storageForm.storage_root_id" placeholder="使用默认根目录" clearable style="width: 100%">
            <el-option
              v-for="r in storageRoots"
              :key="r.id"
              :label="`${r.name}（${r.path}）`"
              :value="r.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <p class="storage-tip">
        子目录结构由「关注列表」里每个来源的模板决定，例如 {{ '{platform}/{account}/{creator}/{date}' }}。
      </p>
      <template #footer>
        <el-button @click="storageDialog = false">取消</el-button>
        <el-button type="primary" :loading="storageSaving" @click="saveStorage">保存</el-button>
      </template>
    </el-dialog>

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
        <img
          v-if="qrImage && !qrBlocked"
          :src="`data:image/png;base64,${qrImage}`"
          alt="二维码"
          class="qr-img"
        />
        <p class="qr-status" :class="qrStatus">{{ qrMessage }}</p>
        <p
          v-if="qrStatus === 'pending' || qrStatus === 'scanned' || qrStatus === 'confirmed'"
          class="qr-countdown"
          :class="{ warn: qrRemaining <= 60 }"
        >
          二维码有效期剩余 <b>{{ countdownText }}</b>
        </p>
        <p v-if="qrStatus === 'expired'" class="qr-hint">二维码已失效，点下方按钮重新获取</p>
        <p v-else-if="qrStatus === 'error' && !qrBlocked" class="qr-hint">
          本次扫码未能完成，点下方按钮重新获取
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
const togglingSync = ref<number | null>(null)
const dialog = ref(false)
const submitting = ref(false)
const editing = ref<Account | null>(null)
const mode = ref<'qr' | 'manual'>('qr')

const qrDialog = ref(false)
const qrImage = ref('')
const qrMessage = ref('')
const qrStatus = ref('pending')
// 平台要求「去 App 完成安全验证」时置 true：此时二维码已无用（再扫也不放行），
// 界面只留说明与重试入口，避免用户对着已经失效的码反复扫。
const qrBlocked = ref(false)
let pollTimer: number | undefined
let pollErrors = 0
// 当前正在轮询的会话 id。轮询回调靠它丢弃"上一轮会话"的迟到响应——
// 否则用户点两次「获取二维码」后，旧会话的定时器会把「会话不存在或已过期」
// 写回同一个界面，盖掉正在正常推进的新会话（真机：扫了、确认了，界面却报会话不存在）。
let activeSessionId = ''
// 二维码倒计时。**展示的是屏幕上那张码的寿命**，不是会话总时长：后端换码时随新图
// 回传 qr_image_expires_in（抖音实测 ~59s），拿不到才退回会话 TTL。
const qrRemaining = ref(0)
let countdownTimer: number | undefined

const countdownText = computed(() => {
  const s = Math.max(0, qrRemaining.value)
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m}:${String(sec).padStart(2, '0')}`
})

function stopCountdown() {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = undefined
  }
}

function startCountdown(seconds: number) {
  stopCountdown()
  qrRemaining.value = Math.max(0, Math.floor(seconds || 0))
  countdownTimer = window.setInterval(() => {
    if (qrRemaining.value > 0) {
      qrRemaining.value -= 1
    } else {
      // 到点即停：本地先反映失效，服务端轮询结果以同样语义收口。
      stopCountdown()
    }
  }, 1000)
}

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
  // 先清掉上一轮：否则旧定时器会继续轮询已销毁的会话，
  // 每 3 秒把「会话不存在或已过期」写回界面，盖掉新会话的进度。
  stopPolling()
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
    qrBlocked.value = false
    qrDialog.value = true
    pollErrors = 0
    // 倒计时按"这张码的寿命"走（抖音 ~59s）；后端没给就退回会话 TTL。
    startCountdown(data.qr_image_expires_in ?? data.expires_in)
    activeSessionId = data.session_id
    scheduleNextPoll(data.session_id)
  } finally {
    submitting.value = false
  }
}

// 串行轮询：每次都等上一轮响应回来再排下一轮。
// 用 setInterval 的话，后端一轮最多要等 3s（抓二维码有超时窗口），会与下一次请求
// 重叠——同一个会话被并发轮询时结果会乱序落地，用户看到的消息会来回跳（真机症状：
// 明明已确认，界面却弹出"会话不存在或已过期"）。
function scheduleNextPoll(sessionId: string) {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(() => void poll(sessionId), 3000)
}

async function poll(sessionId: string) {
  // 迟到的旧会话响应直接丢弃：它的结果已经和当前界面无关。
  if (sessionId !== activeSessionId) return
  // 连续失败才停：偶发一次网络抖动不该中断整个扫码流程
  try {
    const { data } = await api.get(`/api/accounts/qr-login/${sessionId}`)
    if (sessionId !== activeSessionId) return
    pollErrors = 0
    qrMessage.value = data.message
    qrStatus.value = data.status
    // 后端把"平台要求去 App 验证"也归为 error；据此收起二维码并停止倒计时误导。
    qrBlocked.value =
      data.status === 'error' && typeof data.message === 'string' && data.message.includes('验证')
    // 平台二维码会自然过期并被登录页换新（抖音约 60s）；后端把新码随轮询回传，
    // 这里换图即可继续扫，不必让用户重开弹窗（倒计时另按这批新码重新计）。
    // 状态含义：pending=等待扫码 / scanned=已扫待手机确认 / confirmed=已确认，
    // 后端正在等登录态落盘（此时别当成"没反应"，页面会显示对应提示）。
    if (data.qr_image_base64) {
      qrImage.value = data.qr_image_base64
      // 换码 = 屏幕上换了一张新码，倒计时必须跟着重置，否则显示的还是旧码的剩余时间。
      if (data.qr_image_expires_in) startCountdown(data.qr_image_expires_in)
    }
    if (data.status === 'success') {
      stopPolling()
      qrDialog.value = false
      ElMessage.success('扫码登录成功')
      await load()
      return
    } else if (data.status === 'expired' || data.status === 'error') {
      stopPolling()
      if (data.status === 'expired') {
        qrRemaining.value = 0
        ElMessage.warning('二维码已超时，请重新获取')
      }
      return
    }
  } catch (e: any) {
    if (sessionId !== activeSessionId) return
    pollErrors += 1
    if (pollErrors >= 3) {
      stopPolling()
      qrStatus.value = 'error'
      qrMessage.value = e?.response?.data?.detail || '轮询失败，请关闭后重新获取二维码'
      return
    }
  }
  // 非终态：排下一轮（在响应之后，保证串行）。
  if (sessionId === activeSessionId) scheduleNextPoll(sessionId)
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = undefined
  }
  // 停掉就把当前会话标记作废：任何已在途的响应回来时都会被丢弃。
  activeSessionId = ''
  stopCountdown()
}

async function toggleSync(row: Account) {
  const next = !row.sync_enabled
  togglingSync.value = row.id
  try {
    await api.put(`/api/accounts/${row.id}/sync-enabled`, { enabled: next })
    row.sync_enabled = next
    ElMessage.success(next ? '已开启该账号同步' : '已进入沉默期，该账号所有同步/调用已禁止')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally {
    togglingSync.value = null
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

// 存储目录设置：账号级根目录绑定（NULL = 默认根）。
const storageDialog = ref(false)
const storageSaving = ref(false)
const storageRoots = ref<Array<{ id: number; name: string; path: string }>>([])
const storageForm = reactive<{ account: Account | null; storage_root_id: number | null }>({
  account: null,
  storage_root_id: null,
})

function openStorage(row: Account) {
  storageForm.account = row
  storageForm.storage_root_id = (row as any).storage_root_id ?? null
  storageDialog.value = true
}

async function saveStorage() {
  if (!storageForm.account) return
  storageSaving.value = true
  try {
    await api.put(`/api/accounts/${storageForm.account.id}/storage`, {
      storage_root_id: storageForm.storage_root_id,
    })
    ElMessage.success('存储目录已更新')
    storageDialog.value = false
    await load()
  } finally {
    storageSaving.value = false
  }
}

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
  storageRoots.value = (await api.get('/api/system/storage-roots')).data
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

/* 同步总开关行 */
.acc-sync {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  background: color-mix(in oklab, var(--ok) 6%, var(--surface));
}
.acc-sync.off {
  border-color: color-mix(in oklab, var(--warn) 45%, var(--line-soft));
  background: var(--warn-soft);
}
.acc-sync-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.acc-sync-title { font-weight: 700; font-size: var(--fs-sm); color: var(--ink); }
.acc-sync-sub { font-size: var(--fs-xs); color: var(--muted); }
.acc-sync .grow { flex: 1; }
.acc-sync .ui-switch[disabled] { opacity: .5; cursor: not-allowed; }

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
.acc-id { margin: 0; font-size: var(--fs-xs); color: var(--muted); font-family: var(--font-mono); }
.storage-cell { display: flex; align-items: center; gap: 6px; min-width: 0; }
.storage-cell .storage-path { max-width: 150px; font-size: var(--fs-xs); color: var(--muted); }
.mono { font-family: var(--font-mono); }
.ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.storage-tip { margin: 0 0 12px; font-size: var(--fs-sm); color: var(--muted); line-height: 1.6; }
.storage-tip code { font-family: var(--font-mono); }
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

.qr-status.scanned,
.qr-status.confirmed {
  color: var(--warn);
  font-weight: 700;
}

.qr-countdown {
  font-size: 13px;
  color: var(--muted);
  margin: 2px 0 10px;
  font-variant-numeric: tabular-nums;
}

.qr-countdown b {
  color: var(--ink-2);
  font-weight: 600;
}

.qr-countdown.warn,
.qr-countdown.warn b {
  color: var(--danger);
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
