<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>关注列表</h1>
      <span class="sub">关注博主与订阅源统一在这里管理</span>
      <div class="grow" />
      <span v-if="cooling" class="ui-badge warn">风控冷却中 · 展示缓存</span>
    </div>

    <!-- 顶层 tab：关注博主 / 订阅源 -->
    <div class="ui-tabs">
      <button class="ui-tab" :class="{ on: topTab === 'creators' }" @click="topTab = 'creators'">关注博主</button>
      <button class="ui-tab" :class="{ on: topTab === 'sources' }" @click="openSources">订阅源</button>
    </div>

    <!-- ============ 关注博主 ============ -->
    <template v-if="topTab === 'creators'">
      <div class="bar">
        <button class="ui-btn primary" :loading="refreshing" :disabled="!currentAccount" @click="refresh(false)">
          {{ refreshing ? '拉取中…' : '刷新关注列表' }}
        </button>
        <el-tooltip content="忽略缓存立即重拉（仍受平台风控冷却限制，请勿频繁点击）">
          <button class="ui-btn" :disabled="refreshing || !currentAccount" @click="refresh(true)">强制刷新</button>
        </el-tooltip>
        <span class="hint">头像 / 昵称来自平台真实数据，点击可跳转博主主页。</span>
        <span class="grow" />
        <span v-if="lastRefresh" class="ui-badge">更新于 {{ fmt(lastRefresh) }}</span>
      </div>

      <!-- 一级 tab：平台 -->
      <div class="ui-tabs">
        <button
          v-for="p in platformsWithAccounts"
          :key="p.platform"
          class="ui-tab"
          :class="{ on: activePlatform === p.platform }"
          @click="switchPlatform(p.platform)"
        >
          {{ p.label }}
          <span v-if="countFor(p.platform)" class="tab-n">{{ countFor(p.platform) }}</span>
        </button>
      </div>

      <!-- 二级 tab：账号 -->
      <div v-if="accountsForPlatform.length > 1" class="ui-chips">
        <button
          v-for="a in accountsForPlatform"
          :key="a.id"
          class="ui-chip"
          :class="{ on: activeAccountId === String(a.id) }"
          @click="switchAccount(String(a.id))"
        >
          {{ a.display_name || a.platform_label }}
        </button>
      </div>
      <div v-else-if="accountsForPlatform.length === 1" class="single-account">
        账号：{{ accountsForPlatform[0].display_name || accountsForPlatform[0].platform_label }}
      </div>

      <div v-loading="loading" class="grid-wrap">
        <div v-if="!loading && !visibleCreators.length" class="ui-empty">
          <div class="big">暂无关注博主</div>
          <p>{{ emptyText }}</p>
        </div>
        <div v-else class="ui-grid">
          <article v-for="c in visibleCreators" :key="c.id" class="ui-card">
            <div class="row">
              <span class="ava" :style="c.avatar ? { backgroundImage: `url(${c.avatar})` } : {}" @click="openHome(c)">
                {{ c.avatar ? '' : (c.author_name || '?').slice(0, 1) }}
              </span>
              <div style="min-width: 0">
                <div class="name">
                  <a v-if="c.home_url" :href="c.home_url" target="_blank" rel="noopener">
                    {{ c.author_name || c.author_id }}
                  </a>
                  <span v-else>{{ c.author_name || c.author_id }}</span>
                  <span v-if="c.verified" class="ui-badge">认证</span>
                </div>
                <div class="stats">
                  粉丝 {{ c.follower_count > 0 ? compact(c.follower_count) : '—' }} ·
                  获赞 {{ c.like_count > 0 ? compact(c.like_count) : '—' }}
                </div>
              </div>
              <div class="grow" />
              <button class="ui-icon-btn" title="立即同步" @click="syncNow(c)">↻</button>
            </div>
            <p v-if="c.signature" class="sig">{{ c.signature }}</p>
            <div class="foot">
              <span class="ui-toggle">
                <span>开启同步</span>
                <button class="ui-switch" :class="{ on: c.sync_enabled }" @click="toggle(c, 'sync_enabled', !c.sync_enabled)">
                  <i />
                </button>
              </span>
              <span v-if="c.sync_enabled" class="ui-toggle">
                <span>全量</span>
                <button class="ui-switch sm" :class="{ on: c.full_sync }" @click="toggle(c, 'full_sync', !c.full_sync)">
                  <i />
                </button>
              </span>
              <span v-else class="ui-badge">仅新发布</span>
              <span class="grow" />
              <span class="last">{{ c.last_synced_at ? `上次 ${fmt(c.last_synced_at)}` : '未同步' }}</span>
            </div>
          </article>
        </div>
      </div>
    </template>

    <!-- ============ 订阅源 ============ -->
    <template v-else>
      <div class="bar">
        <button class="ui-btn primary" @click="openCreate">新建订阅</button>
        <button class="ui-btn" :loading="subLoading" @click="loadSubs">刷新</button>
        <span class="hint">订阅按周期自动同步；B站 / 抖音支持拉取收藏夹、合集与关注列表。</span>
      </div>

      <section class="ui-panel" v-loading="subLoading">
        <div v-if="!subLoading && !subs.length" class="ui-empty">
          <div class="big">还没有订阅源</div>
          <p>点「新建订阅」选收藏夹 / 合集 / 博主，或直接在「关注博主」里开启同步。</p>
        </div>
        <table v-else class="ui-table">
          <thead>
            <tr>
              <th>来源</th><th>平台</th><th>周期</th><th>筛选</th>
              <th>上次同步</th><th style="text-align: right">启用</th><th style="text-align: right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in subs" :key="row.id">
              <td class="strong">
                <span class="src">
                  <span v-if="row.from_followed_creator && row.creator_avatar" class="mini-ava"
                    :style="{ backgroundImage: `url(${row.creator_avatar})` }" />
                  {{ row.title || row.remote_id }}
                </span>
                <span class="src-id">{{ sourceText(row.source_type) }} · {{ row.remote_id }}</span>
              </td>
              <td>{{ subPlatformLabel(row) }}</td>
              <td class="num">{{ row.interval_minutes }} 分</td>
              <td class="src-id">{{ filterSummary(row) }}</td>
              <td class="src-id">{{ row.last_sync_at ? fmt(row.last_sync_at) : '未同步' }}</td>
              <td style="text-align: right">
                <button class="ui-switch" :class="{ on: row.enabled }" @click="toggleSub(row)"><i /></button>
              </td>
              <td style="text-align: right">
                <button class="ui-btn ghost sm" @click="syncSub(row)">同步</button>
                <button class="ui-btn ghost sm" @click="openRuns(row)">历史</button>
                <button class="ui-btn ghost sm" @click="openEdit(row)">编辑</button>
                <button class="ui-btn ghost sm danger" @click="removeSub(row)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- 新建 / 编辑订阅 -->
    <el-dialog v-model="dialog" :title="editing ? '编辑订阅' : '新建订阅'" width="580px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item label="账号">
          <el-select v-model="form.account_id" placeholder="选择已登录账号" style="width: 100%" @change="onFormAccountChange">
            <el-option v-for="a in accounts" :key="a.id" :label="`${a.platform_label} - ${a.display_name || a.id}`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源类型">
          <el-select v-model="form.source_type" style="width: 100%" @change="onSourceTypeChange">
            <el-option label="收藏夹" value="favorite" />
            <el-option label="合集" value="collection" />
            <el-option label="关注博主" value="creator" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <div style="width: 100%">
            <el-radio-group v-model="subPickMode" size="small" style="margin-bottom: 6px" @change="onPickModeChange">
              <el-radio-button value="list">从列表选择</el-radio-button>
              <el-radio-button value="manual">手动填 ID</el-radio-button>
            </el-radio-group>
            <el-select
              v-if="subPickMode === 'list'"
              v-model="form.remote_id"
              :loading="sourcesLoading"
              :no-data-text="sourceEmptyText"
              filterable
              placeholder="选择要跟踪的收藏夹 / 合集 / 博主"
              style="width: 100%"
            >
              <el-option v-for="s in sources" :key="s.remote_id" :label="s.title || s.remote_id" :value="s.remote_id" />
            </el-select>
            <el-input v-else v-model="form.remote_id" placeholder="收藏夹ID / 合集ID / UP主mid / 博主sec_uid" />
          </div>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.title" placeholder="例如：技术收藏夹（可留空）" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
        <el-form-item label="自动下载"><el-switch v-model="form.auto_download" /></el-form-item>
        <el-form-item label="周期(分钟)"><el-input-number v-model="form.interval_minutes" :min="10" :step="10" /></el-form-item>
        <el-form-item label="最低播放量">
          <el-input-number v-model="form.min_play" :min="0" :step="1000" />
          <span class="detail">0 = 不限</span>
        </el-form-item>
        <el-form-item label="最近天数">
          <el-input-number v-model="form.publish_after_days" :min="0" />
          <span class="detail">只同步最近 N 天发布的内容，0 = 不限</span>
        </el-form-item>
        <el-form-item label="单次上限">
          <el-input-number v-model="form.max_items" :min="1" :max="500" />
          <span class="detail">单次同步最多入库条数（1~500）</span>
        </el-form-item>
        <el-form-item label="只同步新"><el-switch v-model="form.only_new" /></el-form-item>
        <el-form-item label="下载画质">
          <el-select v-model="form.download_quality" style="width: 200px">
            <el-option label="最佳画质" value="best" />
            <el-option label="1080P" value="1080" />
            <el-option label="720P" value="720" />
            <el-option label="仅音频" value="audio" />
          </el-select>
        </el-form-item>
        <el-form-item label="同步评论"><el-switch v-model="form.with_comments" /></el-form-item>
        <el-form-item label="子目录模板">
          <el-input v-model="form.subdir_template" placeholder="{platform}/{creator}/{date}" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 同步历史 -->
    <el-drawer v-model="runsDrawer" :title="`同步历史 · ${runsTitle}`" size="50%">
      <el-table :data="runs" v-loading="runsLoading">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ fmt(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="runStatusType(row.status)">{{ runStatusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fetched" label="抓取" width="80" />
        <el-table-column prop="added" label="新增" width="80" />
        <el-table-column prop="error" label="错误" show-overflow-tooltip />
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { api, type Account } from '../api'

const topTab = ref<'creators' | 'sources'>('creators')

interface FollowedCreator {
  id: number
  account_id: number
  platform: string
  author_id: string
  author_name: string
  avatar: string
  home_url: string
  signature: string
  verified: boolean
  follower_count: number
  like_count: number
  sync_enabled: boolean
  full_sync: boolean
  last_synced_at: string | null
}

const PLATFORM_LABELS: Record<string, string> = { douyin: '抖音', bilibili: 'B站' }
// 关注列表页只覆盖已支持拉取的平台
const SUPPORTED = ['douyin', 'bilibili']

const accounts = ref<Account[]>([])
const allCreators = ref<FollowedCreator[]>([])
const loading = ref(false)
const refreshing = ref(false)
const busy = ref<number | null>(null)
const syncing = ref<number | null>(null)
const lastRefresh = ref<string | null>(null)
const cooling = ref(false)
const activePlatform = ref('douyin')
const activeAccountId = ref<string>('')

const platformsWithAccounts = computed(() =>
  SUPPORTED.filter((p) => accounts.value.some((a) => a.platform === p)).map((p) => ({
    platform: p,
    label: PLATFORM_LABELS[p] ?? p,
  })),
)

const accountsForPlatform = computed(() =>
  accounts.value.filter((a) => a.platform === activePlatform.value),
)

const currentAccount = computed(() =>
  accountsForPlatform.value.find((a) => String(a.id) === activeAccountId.value),
)

const emptyText = computed(() => {
  if (!currentAccount.value) return '该平台暂无可用的已登录账号'
  return '暂无关注博主，点「刷新关注列表」从平台拉取'
})

const visibleCreators = computed(() =>
  allCreators.value.filter(
    (c) => c.platform === activePlatform.value && c.account_id === currentAccount.value?.id,
  ),
)

function countFor(platform: string) {
  return allCreators.value.filter((c) => c.platform === platform).length
}
function compact(n: number) {
  return n >= 10000 ? `${(n / 10000).toFixed(n % 10000 === 0 ? 0 : 1)}万` : `${n}`
}
function fmt(v: string) {
  return new Date(v.endsWith('Z') ? v : v + 'Z').toLocaleString('zh-CN', { hour12: false })
}
function openHome(c: FollowedCreator) {
  if (c.home_url) window.open(c.home_url, '_blank', 'noopener')
}

async function loadAccounts() {
  accounts.value = (await api.get('/api/accounts')).data
  // 选中第一个有账号的平台
  const first = platformsWithAccounts.value[0]
  if (first) activePlatform.value = first.platform
  pickAccount()
}

function pickAccount() {
  const list = accountsForPlatform.value
  if (!activeAccountId.value || !list.some((a) => String(a.id) === activeAccountId.value)) {
    activeAccountId.value = list.length ? String(list[0].id) : ''
  }
}

async function load() {
  // 一次性拉全量（所有已入库的关注博主），前端按平台/账号过滤，保证 tab 计数准确。
  loading.value = true
  try {
    allCreators.value = (await api.get('/api/followed-creators')).data
  } finally {
    loading.value = false
  }
}

async function refresh(force: boolean) {
  if (!currentAccount.value) return
  refreshing.value = true
  try {
    const { data } = await api.post('/api/followed-creators/refresh', {
      account_id: currentAccount.value.id,
      force,
    })
    cooling.value = !!data.cooling
    if (data.cooling) {
      ElMessage({ message: '平台风控冷却中，当前展示缓存数据', type: 'warning' })
    } else if (data.cached) {
      ElMessage.info(`使用了缓存（${data.count} 位）`)
    } else {
      ElMessage.success(`已更新 ${data.count} 位关注博主`)
      lastRefresh.value = new Date().toISOString()
    }
    await load()
  } catch (e: any) {
    // 429 = 风控冷却中（无缓存可展示）；标记冷却态并保留现有列表
    if (e?.response?.status === 429) {
      cooling.value = true
    }
  } finally {
    refreshing.value = false
  }
}

async function toggle(c: FollowedCreator, field: 'sync_enabled' | 'full_sync', value: boolean) {
  busy.value = c.id
  try {
    const payload: Record<string, boolean> = {
      sync_enabled: field === 'sync_enabled' ? value : c.sync_enabled,
      full_sync: field === 'full_sync' ? value : c.full_sync,
    }
    const { data } = await api.put(`/api/followed-creators/${c.id}`, payload)
    Object.assign(c, data)
  } finally {
    busy.value = null
  }
}

async function syncNow(c: FollowedCreator) {
  syncing.value = c.id
  try {
    const { data } = await api.post(`/api/followed-creators/${c.id}/sync`)
    if (data.status === 'success') {
      ElMessage.success(`同步完成：抓取 ${data.fetched} 条，新增 ${data.added} 条`)
    } else {
      ElMessage({ message: data.error || `同步未完成（${data.status}）`, type: 'warning' })
    }
    await load()
  } finally {
    syncing.value = null
  }
}

/* ---------- 订阅源（原「订阅同步」页，合并进关注列表） ---------- */
interface Subscription {
  id: number
  account_id: number
  platform: string
  source_type: string
  remote_id: string
  title: string
  enabled: boolean
  auto_download: boolean
  interval_minutes: number
  subdir_template: string
  min_play: number
  publish_after_days: number
  max_items: number
  only_new: boolean
  download_quality: string
  with_comments: boolean
  last_sync_at: string | null
  from_followed_creator: boolean
  creator_avatar: string
}
interface RemoteSource {
  remote_id: string
  title: string
}
interface SyncRunRow {
  id: number
  started_at: string | null
  status: string
  fetched: number
  added: number
  error: string
}

const SOURCE_TYPES: Record<string, string> = { favorite: '收藏夹', collection: '合集', creator: '关注博主', like: '点赞' }
const QUALITY_LABELS: Record<string, string> = { best: '最佳', '1080': '1080P', '720': '720P', audio: '仅音频' }
const DEFAULT_SUBDIR = '{platform}/{creator}/{date}'

const subs = ref<Subscription[]>([])
const subLoading = ref(false)
const subSyncing = ref<number | null>(null)
const dialog = ref(false)
const editing = ref<Subscription | null>(null)
const submitting = ref(false)
const subPickMode = ref<'list' | 'manual'>('list')
const sources = ref<RemoteSource[]>([])
const sourcesLoading = ref(false)
const sourcesError = ref('')
let sourcesSeq = 0
const runsDrawer = ref(false)
const runsTitle = ref('')
const runs = ref<SyncRunRow[]>([])
const runsLoading = ref(false)
let runsSubId: number | null = null

const form = reactive({
  account_id: null as number | null,
  source_type: 'favorite',
  remote_id: '',
  title: '',
  enabled: true,
  auto_download: false,
  interval_minutes: 360,
  subdir_template: DEFAULT_SUBDIR,
  min_play: 0,
  publish_after_days: 0,
  max_items: 50,
  only_new: true,
  download_quality: 'best',
  with_comments: false,
})

const sourceEmptyText = computed(() => {
  if (!form.account_id) return '请先选择账号'
  if (sourcesError.value) return sourcesError.value
  return '该账号在此类型下没有可订阅的源'
})

function sourceText(v: string) { return SOURCE_TYPES[v] ?? v }
function subPlatformLabel(row: Subscription) {
  const acc = accounts.value.find((a) => a.id === row.account_id)
  return acc?.platform_label || PLATFORM_LABELS[row.platform] || row.platform
}
function filterSummary(row: Subscription) {
  const play = row.min_play > 0 ? `播放≥${compact(row.min_play)}` : '播放不限'
  const days = row.publish_after_days > 0 ? `最近${row.publish_after_days}天` : '时间不限'
  return `${play} · ${days} · 上限${row.max_items}`
}
function runStatusType(s: string) {
  return ({ success: 'success', error: 'danger' } as Record<string, string>)[s] ?? 'warning'
}
function runStatusText(s: string) {
  return ({ success: '成功', error: '失败', running: '进行中' } as Record<string, string>)[s] ?? s
}

async function openSources() {
  topTab.value = 'sources'
  if (!subs.value.length) await loadSubs()
}

async function loadSubs() {
  subLoading.value = true
  try {
    subs.value = (await api.get('/api/subscriptions')).data
  } finally {
    subLoading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    account_id: null, source_type: 'favorite', remote_id: '', title: '',
    enabled: true, auto_download: false, interval_minutes: 360,
    subdir_template: DEFAULT_SUBDIR, min_play: 0, publish_after_days: 0,
    max_items: 50, only_new: true, download_quality: 'best', with_comments: false,
  })
  sources.value = []
  sourcesError.value = ''
}

function openCreate() {
  editing.value = null
  resetForm()
  subPickMode.value = 'list'
  dialog.value = true
}

function openEdit(row: Subscription) {
  editing.value = row
  Object.assign(form, {
    account_id: row.account_id, source_type: row.source_type, remote_id: row.remote_id,
    title: row.title, enabled: row.enabled, auto_download: row.auto_download,
    interval_minutes: row.interval_minutes, subdir_template: row.subdir_template || DEFAULT_SUBDIR,
    min_play: row.min_play, publish_after_days: row.publish_after_days, max_items: row.max_items,
    only_new: row.only_new, download_quality: row.download_quality, with_comments: row.with_comments,
  })
  subPickMode.value = 'manual'
  sources.value = []
  sourcesError.value = ''
  dialog.value = true
}

function onFormAccountChange() {
  form.remote_id = ''
  sources.value = []
  sourcesError.value = ''
  if (subPickMode.value === 'list') loadSources()
}

function onSourceTypeChange() {
  form.remote_id = ''
  sources.value = []
  sourcesError.value = ''
  if (subPickMode.value === 'list') loadSources()
}

function onPickModeChange() {
  if (subPickMode.value === 'list') loadSources()
}

async function loadSources() {
  if (!form.account_id) {
    sources.value = []
    return
  }
  const seq = ++sourcesSeq
  sourcesLoading.value = true
  sourcesError.value = ''
  try {
    const { data } = await api.get(`/api/accounts/${form.account_id}/remote-sources`, {
      params: { source_type: form.source_type },
    })
    if (seq !== sourcesSeq) return
    sources.value = Array.isArray(data) ? data : []
    if (!sources.value.length) sourcesError.value = '没拉到可订阅的源，可切「手动填 ID」'
  } catch {
    if (seq !== sourcesSeq) return
    sources.value = []
    sourcesError.value = '加载失败，可切「手动填 ID」'
  } finally {
    if (seq === sourcesSeq) sourcesLoading.value = false
  }
}

function validate(): string | null {
  if (!form.account_id) return '请选择账号'
  if (!form.remote_id || !form.remote_id.trim()) return subPickMode.value === 'list' ? '请选择来源' : '请填写来源 ID'
  const interval = Number(form.interval_minutes)
  if (!Number.isFinite(interval) || interval < 10) return '同步周期至少 10 分钟'
  if (!(form.download_quality in QUALITY_LABELS)) return '下载画质不合法'
  const maxItems = Number(form.max_items)
  if (!Number.isFinite(maxItems) || maxItems < 1 || maxItems > 500) return '单次上限需在 1~500 之间'
  return null
}

async function submit() {
  const err = validate()
  if (err) {
    ElMessage.warning(err)
    return
  }
  submitting.value = true
  try {
    const payload = { ...form, remote_id: form.remote_id.trim() }
    if (editing.value) {
      await api.put(`/api/subscriptions/${editing.value.id}`, payload)
      ElMessage.success('已保存')
    } else {
      await api.post('/api/subscriptions', payload)
      ElMessage.success('已创建')
    }
    dialog.value = false
    await loadSubs()
  } finally {
    submitting.value = false
  }
}

function rowPayload(row: Subscription, patch: Partial<Subscription> = {}) {
  return {
    account_id: row.account_id, source_type: row.source_type, remote_id: row.remote_id,
    title: row.title, enabled: row.enabled, auto_download: row.auto_download,
    interval_minutes: row.interval_minutes, subdir_template: row.subdir_template,
    min_play: row.min_play, publish_after_days: row.publish_after_days, max_items: row.max_items,
    only_new: row.only_new, download_quality: row.download_quality, with_comments: row.with_comments,
    ...patch,
  }
}

async function syncSub(row: Subscription) {
  subSyncing.value = row.id
  try {
    const { data } = await api.post(`/api/subscriptions/${row.id}/sync`)
    if (data.status === 'success' && !data.error) {
      ElMessage.success(`同步完成：抓取 ${data.fetched} 条，新增 ${data.added} 条`)
    } else {
      ElMessage({ message: data.error || `同步未完成（${data.status}）`, type: 'warning' })
    }
    await loadSubs()
  } finally {
    subSyncing.value = null
  }
}

async function toggleSub(row: Subscription) {
  await api.put(`/api/subscriptions/${row.id}`, rowPayload(row, { enabled: !row.enabled }))
  await loadSubs()
}

async function removeSub(row: Subscription) {
  if (!window.confirm(`删除订阅「${row.title || row.remote_id}」？同步历史会一并删除。`)) return
  await api.delete(`/api/subscriptions/${row.id}`)
  ElMessage.success('已删除')
  if (runsSubId === row.id) runsDrawer.value = false
  await loadSubs()
}

async function loadRuns(subId: number) {
  runsLoading.value = true
  try {
    runs.value = (await api.get(`/api/subscriptions/${subId}/runs`)).data
  } finally {
    runsLoading.value = false
  }
}

async function openRuns(row: Subscription) {
  runsSubId = row.id
  runsTitle.value = row.title || row.remote_id
  runs.value = []
  runsDrawer.value = true
  await loadRuns(row.id)
}

async function switchPlatform(platform: string) {
  activePlatform.value = platform
  activeAccountId.value = ''
  pickAccount()
  await load()
}

async function switchAccount(id: string) {
  activeAccountId.value = id
  await load()
}

onMounted(async () => {
  await loadAccounts()
  await load()
})
</script>

<style scoped>
.ui-page { display: flex; flex-direction: column; gap: var(--sp-5); }
.bar { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.bar .grow { margin-left: auto; }
.hint { font-size: var(--fs-sm); color: var(--muted); }

.tab-n {
  margin-left: 6px; font-size: var(--fs-xs); font-weight: 700;
  background: var(--accent); color: var(--accent-ink); padding: 0 5px;
}

.ui-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.single-account { font-size: var(--fs-sm); color: var(--muted); }

/* 关注博主卡片（复用 ui.css 的 .ui-card，这里只调细节） */
.grid-wrap { min-height: 120px; }
.ui-card .ava {
  width: 54px; height: 54px; border-radius: 0; flex: none; cursor: pointer;
  display: grid; place-items: center; color: #fff; font-weight: 700; font-size: var(--fs-lg);
  background-color: var(--surface-3); background-size: cover; background-position: center;
  border: 1px solid var(--ink);
}
.ui-card .name { display: flex; align-items: center; gap: 6px; font-weight: 650; }
.ui-card .name a { color: inherit; text-decoration: none; }
.ui-card .name a:hover { color: var(--accent); text-decoration: underline; }
.ui-card .stats { color: var(--muted); font-size: var(--fs-sm); margin-top: 2px; }
.ui-card .sig {
  margin: 11px 0 0; font-size: var(--fs-sm); color: var(--ink-2);
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; min-height: 34px;
}
.ui-card .foot {
  margin-top: 12px; padding-top: 11px; border-top: 2px solid var(--ink);
  display: flex; align-items: center; gap: 14px; font-size: var(--fs-sm); color: var(--muted);
}
.ui-card .foot .grow { margin-left: auto; }
.ui-toggle { display: inline-flex; align-items: center; gap: 7px; }
.ui-icon-btn {
  width: 30px; height: 30px; display: grid; place-items: center;
  border: 1px solid var(--ink); background: var(--surface); color: var(--ink-2);
  cursor: pointer; font-size: 15px;
}
.ui-icon-btn:hover { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.ui-switch {
  width: 36px; height: 20px; border-radius: 999px; background: var(--surface-3);
  border: 1px solid var(--ink); position: relative; cursor: pointer; padding: 0; flex: none;
  transition: background .16s ease-out;
}
.ui-switch i {
  position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 999px;
  background: var(--surface); border: 1px solid var(--ink);
  transition: transform .16s cubic-bezier(.16,1,.3,1);
}
.ui-switch.on { background: var(--accent); }
.ui-switch.on i { transform: translateX(16px); }
.ui-switch.sm { width: 30px; height: 17px; }
.ui-switch.sm i { width: 11px; height: 11px; }
.ui-switch.sm.on i { transform: translateX(13px); }
.last { font-variant-numeric: tabular-nums; }

/* 订阅源表格 */
.src { display: flex; align-items: center; gap: 6px; }
.src-id { font-size: var(--fs-xs); color: var(--muted); }
.mini-ava {
  width: 18px; height: 18px; flex: none; border: 1px solid var(--ink);
  background-size: cover; background-position: center;
}
.ui-btn.danger { color: var(--danger); }
.detail { margin-left: 8px; font-size: var(--fs-sm); color: var(--muted); }
</style>
