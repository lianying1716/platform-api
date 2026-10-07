<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>关注列表</h1>
      <span class="sub">这里是我跟踪的所有来源 —— 博主、收藏夹、合集。一条记录就是一个同步目标。</span>
      <div class="grow" />
      <span v-if="cooling" class="ui-badge warn">风控冷却中 · 展示缓存</span>
    </div>

    <div class="bar">
      <button class="ui-btn primary" @click="openCreate">新建跟踪</button>
      <el-tooltip content="从平台拉取「我关注的人」，拉回后默认不开启同步，你想同步哪个再打开">
        <button class="ui-btn" :loading="refreshing" :disabled="!currentAccount" @click="refresh(false)">
          {{ refreshing ? '拉取中…' : '导入我的关注' }}
        </button>
      </el-tooltip>
      <el-tooltip content="忽略缓存立即重拉（仍受平台风控冷却限制，请勿频繁点击）">
        <button class="ui-btn" :disabled="refreshing || !currentAccount" @click="refresh(true)">强制刷新</button>
      </el-tooltip>
      <span class="grow" />
      <span class="ui-badge">跟踪中 {{ rows.length }}</span>
      <span class="ui-badge ok">已开启同步 {{ enabledCount }}</span>
    </div>

    <!-- 一级 tab：平台（只在有账号的平台里出现） -->
    <div class="ui-tabs">
      <button
        v-for="p in platformsWithAccounts"
        :key="p.platform"
        class="ui-tab"
        :class="{ on: activePlatform === p.platform }"
        @click="switchPlatform(p.platform)"
      >
        <PlatformMark :platform="p.platform" :show-label="false" size="sm" />
        {{ p.label }}
        <span v-if="countFor(p.platform)" class="tab-n">{{ countFor(p.platform) }}</span>
      </button>
    </div>

    <!-- 二级：账号 -->
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

    <!-- 类型筛选 -->
    <div class="ui-chips">
      <button
        v-for="t in typeFilters"
        :key="t.value"
        class="ui-chip"
        :class="{ on: activeType === t.value }"
        @click="activeType = t.value"
      >
        {{ t.label }}
        <span v-if="t.value !== 'all' && typeCount(t.value)" class="chip-n">{{ typeCount(t.value) }}</span>
      </button>
    </div>

    <div v-loading="loading" class="grid-wrap">
      <div v-if="!loading && !visibleRows.length" class="ui-empty">
        <div class="big">这个账号下还没有跟踪任何来源</div>
        <p>
          用「导入我的关注」把平台上的关注博主拉进来，或点「新建跟踪」手动添加收藏夹 / 合集 / 博主。
        </p>
      </div>

      <!-- 一个列表：每条 = 一个我跟踪的来源。博主显示头像，收藏夹/合集显示类型图标。 -->
      <div v-else class="ui-grid">
        <article v-for="row in visibleRows" :key="row.id" class="ui-card" :class="{ off: !row.enabled }">
          <div class="row">
            <Avatar
              v-if="isCreator(row)"
              class="ava"
              :src="row.creator_avatar"
              :name="row.author_name || row.title || row.remote_id"
              :size="54"
              @click="openHome(row)"
            />
            <span v-else class="ava src-ava" :title="sourceText(row.source_type)">
              {{ sourceGlyph(row.source_type) }}
            </span>
            <div style="min-width: 0">
              <div class="name">
                <a v-if="isCreator(row) && row.home_url" :href="row.home_url" target="_blank" rel="noopener">
                  {{ row.author_name || row.title || row.remote_id }}
                </a>
                <span v-else>{{ row.author_name || row.title || row.remote_id }}</span>
                <span v-if="row.verified" class="ui-badge">认证</span>
                <span class="ui-badge type">{{ sourceText(row.source_type) }}</span>
              </div>
              <div class="stats">
                <template v-if="isCreator(row)">
                  粉丝 {{ (row.follower_count || 0) > 0 ? compact(row.follower_count || 0) : '—' }}
                </template>
                <template v-else>
                  {{ row.remote_id }} · 每 {{ row.interval_minutes }} 分
                </template>
              </div>
            </div>
            <div class="grow" />
            <PlatformMark :platform="row.platform" :show-label="false" size="sm" muted />
            <button class="ui-icon-btn" title="立即同步" @click="syncRow(row)">↻</button>
          </div>

          <p v-if="isCreator(row) && row.signature" class="sig">{{ row.signature }}</p>
          <p v-else-if="!isCreator(row)" class="sig">{{ filterSummary(row) }}</p>

          <div class="foot">
            <span class="ui-toggle">
              <span>开启同步</span>
              <button class="ui-switch" :class="{ on: row.enabled }" @click="toggleEnabled(row)">
                <i />
              </button>
            </span>
            <span v-if="row.enabled" class="ui-toggle">
              <span>全量</span>
              <button class="ui-switch sm" :class="{ on: !row.only_new }" @click="toggleFull(row)">
                <i />
              </button>
            </span>
            <span v-else class="ui-badge">仅收藏</span>
            <span class="grow" />
            <button class="ui-btn ghost sm" @click="openRuns(row)">历史</button>
            <button class="ui-btn ghost sm" @click="openEdit(row)">设置</button>
            <button class="ui-btn ghost sm danger" @click="confirmRemove(row)">移除</button>
          </div>
          <div class="foot-meta">
            <span class="last">{{ row.last_sync_at ? `上次同步 ${fmt(row.last_sync_at)}` : '未同步' }}</span>
          </div>
        </article>
      </div>
    </div>

    <!-- 新建 / 编辑 -->
    <el-dialog v-model="dialog" :title="editing ? '编辑跟踪' : '新建跟踪'" width="580px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item label="账号" v-if="!editing">
          <el-select v-model="form.account_id" placeholder="选择已登录账号" style="width: 100%" @change="onFormAccountChange">
            <el-option v-for="a in accounts" :key="a.id" :label="`${a.platform_label} - ${a.display_name || a.id}`" :value="a.id">
              <PlatformMark :platform="a.platform" size="sm" />
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="来源类型" v-if="!editing">
          <el-select v-model="form.source_type" style="width: 100%" @change="onSourceTypeChange">
            <el-option label="收藏夹" value="favorite" />
            <el-option label="合集" value="collection" />
            <el-option label="博主" value="creator" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源" v-if="!editing">
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
        <el-form-item label="启用同步"><el-switch v-model="form.enabled" /></el-form-item>
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

    <!-- 移除确认 -->
    <div v-if="removeTarget" class="ui-modal" @click.self="removeTarget = null">
      <div class="ui-modal-box narrow">
        <div class="ui-panel-head"><h2>移除跟踪</h2></div>
        <div class="ui-modal-body">
          <p>
            确定移除「{{ removeTarget.title || removeTarget.remote_id }}」？
            已同步入库的内容不受影响，只是不再自动同步。
          </p>
        </div>
        <div class="ui-modal-foot">
          <button class="ui-btn" @click="removeTarget = null">取消</button>
          <button class="ui-btn danger" @click="doRemove">确认移除</button>
        </div>
      </div>
    </div>

    <!-- 同步历史 -->
    <el-drawer v-model="runsDrawer" :title="`同步历史 · ${runsTitle}`" size="50%">
      <el-table :data="runs" v-loading="runsLoading">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ row.started_at ? fmt(row.started_at) : '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="runStatusType(row.status)" size="small">{{ runStatusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fetched" label="抓取" width="70" />
        <el-table-column prop="added" label="新增" width="70" />
        <el-table-column label="错误">
          <template #default="{ row }">{{ row.error || '—' }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import Avatar from '../components/Avatar.vue'
import PlatformMark from '../components/PlatformMark.vue'
import { api, type Account } from '../api'

/** 统一后的"我跟踪的来源"：博主 = creator 来源，收藏夹/合集 = 其他来源。 */
interface TrackRow {
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
  creator_avatar: string
  // creator 专属（由后端从 creator_profile 合成）
  author_name?: string
  home_url?: string
  signature?: string
  verified?: boolean
  follower_count?: number
}

interface RemoteSource { remote_id: string; title: string }
interface SyncRunRow {
  id: number; started_at: string | null; status: string
  fetched: number; added: number; error: string
}

const PLATFORM_LABELS: Record<string, string> = { douyin: '抖音', bilibili: 'B站' }
const SUPPORTED = ['douyin', 'bilibili']
const SOURCE_TYPES: Record<string, string> = {
  favorite: '收藏夹', collection: '合集', creator: '博主', like: '点赞',
}
const DEFAULT_SUBDIR = '{platform}/{creator}/{date}'

const accounts = ref<Account[]>([])
const rows = ref<TrackRow[]>([])
const loading = ref(false)
const refreshing = ref(false)
const cooling = ref(false)
const activePlatform = ref('douyin')
const activeAccountId = ref('')
const activeType = ref<'all' | 'creator' | 'source'>('all')
const syncing = ref<number | null>(null)

const dialog = ref(false)
const editing = ref<TrackRow | null>(null)
const submitting = ref(false)
const removeTarget = ref<TrackRow | null>(null)
const subPickMode = ref<'list' | 'manual'>('list')
const sources = ref<RemoteSource[]>([])
const sourcesLoading = ref(false)
const sourcesError = ref('')
let sourcesSeq = 0
const runsDrawer = ref(false)
const runsTitle = ref('')
const runs = ref<SyncRunRow[]>([])
const runsLoading = ref(false)

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

const platformsWithAccounts = computed(() =>
  SUPPORTED.filter((p) => accounts.value.some((a) => a.platform === p)).map((p) => ({
    platform: p, label: PLATFORM_LABELS[p] ?? p,
  })),
)
const accountsForPlatform = computed(() =>
  accounts.value.filter((a) => a.platform === activePlatform.value),
)
const currentAccount = computed(() =>
  accountsForPlatform.value.find((a) => String(a.id) === activeAccountId.value),
)

/** 当前账号下的行 */
const accountRows = computed(() =>
  rows.value.filter((r) => String(r.account_id) === activeAccountId.value),
)
const creatorRows = computed(() =>
  accountRows.value.filter((r) => r.source_type === 'creator' && activeType.value !== 'source'),
)
const sourceRows = computed(() =>
  accountRows.value.filter((r) => r.source_type !== 'creator' && activeType.value !== 'creator'),
)
const visibleRows = computed(() => [...creatorRows.value, ...sourceRows.value])
function isCreator(row: TrackRow) { return row.source_type === 'creator' }
/** 收藏夹/合集没有头像，用一个几何符号代替，保证卡片结构一致。 */
function sourceGlyph(t: string) {
  return ({ favorite: '☆', collection: '▤', like: '♥' } as Record<string, string>)[t] ?? '▦'
}
const enabledCount = computed(() => rows.value.filter((r) => r.enabled).length)

const typeFilters = [
  { value: 'all' as const, label: '全部' },
  { value: 'creator' as const, label: '博主' },
  { value: 'source' as const, label: '收藏夹 / 合集' },
]
function typeCount(v: 'creator' | 'source') {
  return accountRows.value.filter((r) =>
    v === 'creator' ? r.source_type === 'creator' : r.source_type !== 'creator').length
}
function countFor(platform: string) {
  return rows.value.filter((r) => r.platform === platform).length
}
const sourceEmptyText = computed(() => {
  if (!form.account_id) return '请先选择账号'
  if (sourcesError.value) return sourcesError.value
  return '该账号在此类型下没有可跟踪的源'
})

function sourceText(v: string) { return SOURCE_TYPES[v] ?? v }
function filterSummary(row: TrackRow) {
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

async function load() {
  loading.value = true
  try {
    // 一次拿全量：关注博主（由后端从订阅+资料合成）与订阅源本来就是同一批数据
    const [subs, creators] = await Promise.all([
      api.get('/api/subscriptions'),
      api.get('/api/followed-creators'),
    ])
    const byId = new Map<number, TrackRow>()
    for (const s of subs.data as TrackRow[]) byId.set(s.id, { ...s })
    // 用 creator 视图补上博主资料字段（头像/主页/签名/粉丝）
    for (const c of creators.data as TrackRow[]) {
      const row = byId.get(c.id)
      if (row) Object.assign(row, c, { source_type: 'creator' })
    }
    rows.value = [...byId.values()]
  } finally {
    loading.value = false
  }
}

async function loadAccounts() {
  accounts.value = (await api.get('/api/accounts')).data
  syncAccountSelection()
}

function syncAccountSelection() {
  const list = accountsForPlatform.value
  if (!activeAccountId.value || !list.some((a) => String(a.id) === activeAccountId.value)) {
    activeAccountId.value = list.length ? String(list[0].id) : ''
  }
}

function switchPlatform(platform: string) {
  activePlatform.value = platform
  syncAccountSelection()
}
function switchAccount(id: string) { activeAccountId.value = id }

async function refresh(force: boolean) {
  if (!currentAccount.value) return
  refreshing.value = true
  try {
    const { data } = await api.post('/api/followed-creators/refresh', {
      account_id: currentAccount.value.id, force,
    })
    cooling.value = !!data.cooling
    if (data.cooling) ElMessage({ message: '平台风控冷却中，当前展示缓存数据', type: 'warning' })
    else if (data.cached) ElMessage.info(`使用了缓存（${data.count} 位）`)
    else ElMessage.success(`已导入 ${data.count} 位关注博主`
      + (data.unfollowed ? `，其中 ${data.unfollowed} 位已取关并关闭同步` : ''))
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '拉取失败')
  } finally {
    refreshing.value = false
  }
}

/** 开关只有一份状态：无论从哪个入口改，都写同一行 */
async function toggleEnabled(row: TrackRow) {
  const next = !row.enabled
  row.enabled = next            // 乐观更新
  try {
    if (row.source_type === 'creator') {
      await api.put(`/api/followed-creators/${row.id}`, {
        sync_enabled: next, full_sync: !row.only_new,
      })
    } else {
      await api.put(`/api/subscriptions/${row.id}`, subPayload(row, { enabled: next }))
    }
  } catch (e: any) {
    row.enabled = !next
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

async function toggleFull(row: TrackRow) {
  const nextFull = row.only_new      // 当前是"只新"→ 打开全量
  row.only_new = !nextFull
  try {
    await api.put(`/api/followed-creators/${row.id}`, {
      sync_enabled: row.enabled, full_sync: nextFull,
    })
  } catch (e: any) {
    row.only_new = nextFull
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

async function syncRow(row: TrackRow) {
  syncing.value = row.id
  try {
    const url = row.source_type === 'creator'
      ? `/api/followed-creators/${row.id}/sync`
      : `/api/subscriptions/${row.id}/sync`
    const { data } = await api.post(url)
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

function openHome(row: TrackRow) {
  if (row.home_url) window.open(row.home_url, '_blank', 'noopener')
}

async function openRuns(row: TrackRow) {
  runsDrawer.value = true
  runsTitle.value = row.title || row.author_name || row.remote_id
  runsLoading.value = true
  try {
    runs.value = (await api.get(`/api/subscriptions/${row.id}/runs`)).data
  } finally {
    runsLoading.value = false
  }
}

function openCreate() {
  editing.value = null
  resetForm()
  if (currentAccount.value) form.account_id = currentAccount.value.id
  dialog.value = true
}

function openEdit(row: TrackRow) {
  editing.value = row
  Object.assign(form, {
    account_id: row.account_id, source_type: row.source_type, remote_id: row.remote_id,
    title: row.title, enabled: row.enabled, auto_download: row.auto_download,
    interval_minutes: row.interval_minutes, subdir_template: row.subdir_template,
    min_play: row.min_play, publish_after_days: row.publish_after_days,
    max_items: row.max_items, only_new: row.only_new,
    download_quality: row.download_quality, with_comments: row.with_comments,
  })
  dialog.value = true
}

function confirmRemove(row: TrackRow) { removeTarget.value = row }

async function doRemove() {
  const row = removeTarget.value
  if (!row) return
  try {
    const url = row.source_type === 'creator'
      ? `/api/followed-creators/${row.id}`
      : `/api/subscriptions/${row.id}`
    await api.delete(url)
    ElMessage.success('已移除')
    removeTarget.value = null
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '移除失败')
  }
}

function subPayload(row: TrackRow, extra: Record<string, unknown> = {}) {
  return {
    account_id: row.account_id, source_type: row.source_type, remote_id: row.remote_id,
    title: row.title, enabled: row.enabled, auto_download: row.auto_download,
    interval_minutes: row.interval_minutes, subdir_template: row.subdir_template,
    min_play: row.min_play, publish_after_days: row.publish_after_days,
    max_items: row.max_items, only_new: row.only_new,
    download_quality: row.download_quality, with_comments: row.with_comments,
    ...extra,
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
  subPickMode.value = 'list'
}

function onPickModeChange() { sources.value = []; sourcesError.value = '' }

function onFormAccountChange() {
  sources.value = []
  if (subPickMode.value === 'list') loadSources()
}
function onSourceTypeChange() {
  form.remote_id = ''
  sources.value = []
  if (subPickMode.value === 'list') loadSources()
}

async function loadSources() {
  const accountId = form.account_id
  if (!accountId) return
  const seq = ++sourcesSeq
  sourcesLoading.value = true
  sourcesError.value = ''
  try {
    const { data } = await api.get(`/api/accounts/${accountId}/remote-sources`, {
      params: { source_type: form.source_type },
    })
    if (seq !== sourcesSeq) return
    sources.value = data
  } catch (e: any) {
    if (seq !== sourcesSeq) return
    sourcesError.value = e?.response?.data?.detail || '拉取列表失败'
  } finally {
    if (seq === sourcesSeq) sourcesLoading.value = false
  }
}

async function submit() {
  if (!editing.value && !form.account_id) return ElMessage.warning('请选择账号')
  if (!editing.value && !form.remote_id.trim()) return ElMessage.warning('请选择或填写来源')
  submitting.value = true
  try {
    if (editing.value) {
      await api.put(`/api/subscriptions/${editing.value.id}`, subPayload(editing.value, {
        title: form.title, enabled: form.enabled, auto_download: form.auto_download,
        interval_minutes: form.interval_minutes, subdir_template: form.subdir_template,
        min_play: form.min_play, publish_after_days: form.publish_after_days,
        max_items: form.max_items, only_new: form.only_new,
        download_quality: form.download_quality, with_comments: form.with_comments,
      }))
    } else {
      await api.post('/api/subscriptions', { ...form })
    }
    ElMessage.success('已保存')
    dialog.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    submitting.value = false
  }
}

function compact(v: number) {
  const n = Number(v || 0)
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`
  return n.toLocaleString('zh-CN')
}
function fmt(v: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(async () => {
  await loadAccounts()
  await load()
})
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.hint { font-size: var(--fs-sm); color: var(--muted); }
.grid-wrap { display: flex; flex-direction: column; gap: 16px; }
.ui-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; }
.ui-card { display: flex; flex-direction: column; gap: 8px; }
.ui-card .row { display: flex; align-items: center; gap: 10px; }
.ui-card .ava { cursor: pointer; }
.ui-card.off { opacity: .72; }
.ui-card.off:hover { opacity: 1; }
.src-ava {
  display: grid; place-items: center; cursor: default;
  background: var(--surface-3); border: 1px solid var(--ink);
  font-size: 22px; color: var(--ink-2);
}
.ui-card .name .type { font-weight: 600; }
.ui-card .name { display: flex; align-items: center; gap: 6px; font-weight: 700; min-width: 0; }
.ui-card .name a { color: inherit; text-decoration: none; }
.ui-card .name a:hover { text-decoration: underline; }
.ui-card .stats { font-size: var(--fs-sm); color: var(--muted); margin-top: 2px; }
.sig {
  font-size: var(--fs-sm); color: var(--ink-2); margin: 0;
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden;
}
.foot { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.foot-meta { display: flex; gap: 6px; font-size: var(--fs-xs); color: var(--muted); }
.ui-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-sm); }
.last { font-size: var(--fs-xs); color: var(--muted); }
.src { display: block; }
.src-id { font-size: var(--fs-xs); color: var(--muted); }
.chip-n {
  margin-left: 5px; font-size: 11px; font-weight: 700;
  padding: 0 4px; background: var(--accent-soft); color: var(--accent);
}
.detail { margin-left: 8px; font-size: var(--fs-sm); color: var(--muted); }
</style>
