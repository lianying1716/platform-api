<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>关注列表</h1>
      <span class="sub">跟踪我关注的博主 / 收藏夹 / 合集。同步总开关在「账号中心」每个账号的卡片上。</span>
      <div class="grow" />
      <span v-if="cooling" class="ui-badge warn">风控冷却中 · 展示缓存</span>
    </div>

    <div class="bar">
      <button class="ui-btn primary" @click="openCreate">新建跟踪</button>
      <el-tooltip :content="canImportFollowings
        ? '从平台拉取「我关注的人」。关注列表默认开始跟踪；某个博主名下视频要单独手动开启'
        : '小红书 / 知乎暂不支持自动导入关注列表，请用「新建跟踪」手动填写博主 ID'">
        <button class="ui-btn" :loading="refreshing" :disabled="!currentAccount || !canImportFollowings" @click="refresh(false)">
          {{ refreshing ? '拉取中…' : '导入我的关注' }}
        </button>
      </el-tooltip>
      <el-tooltip content="忽略缓存立即重拉（仍受平台风控冷却限制，请勿频繁点击）">
        <button class="ui-btn" :disabled="refreshing || !currentAccount || !canImportFollowings" @click="refresh(true)">强制刷新</button>
      </el-tooltip>
      <span class="grow" />
      <span class="ui-badge">跟踪中 {{ accountRows.length }}</span>
      <span class="ui-badge ok">已开启 {{ enabledCount }}</span>
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
              :src="row.avatar_url"
              :name="row.author_name || row.title || row.remote_id"
              :size="54"
              @click="openHome(row)"
            />
            <span v-else class="ava src-ava" :title="sourceText(row.source_type)">
              {{ sourceGlyph(row.source_type) }}
            </span>
            <div style="min-width: 0">
              <div class="name">
                <a v-if="isCreator(row) && safeExternalUrl(row.home_url)" :href="safeExternalUrl(row.home_url)" target="_blank" rel="noopener noreferrer">
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
                  {{ row.remote_id }} · {{ row.interval_minutes == null ? '继承全局间隔' : `每 ${row.interval_minutes} 分` }}
                </template>
              </div>
            </div>
            <div class="grow" />
            <PlatformMark :platform="row.platform" :show-label="false" size="sm" muted />
            <button
              class="ui-icon-btn"
              title="立即同步"
              :disabled="syncing === row.id"
              @click="syncRow(row)"
            >{{ syncing === row.id ? '…' : '↻' }}</button>
          </div>

          <!-- 博主：两条独立链路。1) 关注关系默认跟踪；2) 博主名下视频要手动开关。 -->
          <template v-if="isCreator(row)">
            <div class="sync-bar" :class="{ on: row.sync_videos }">
              <span class="sync-label">
                <span class="ui-dot" :class="row.sync_videos ? 'ok' : ''" />
                {{ row.sync_videos ? '视频同步已开启' : '视频同步未开启' }}
              </span>
              <span class="grow" />
              <button
                class="ui-switch lg"
                :class="{ on: row.sync_videos }"
                :aria-label="row.sync_videos ? '关闭视频同步' : '开启视频同步'"
                @click="toggleVideos(row)"
              >
                <i />
              </button>
            </div>
            <div class="relation-bar">
              <span class="relation-text">
                关注关系：{{ row.sync_enabled ? '跟踪中' : '已暂停' }}
              </span>
              <span class="grow" />
              <button
                class="ui-switch sm"
                :class="{ on: row.sync_enabled }"
                :aria-label="row.sync_enabled ? '暂停跟踪关注关系' : '恢复跟踪关注关系'"
                @click="toggleEnabled(row)"
              >
                <i />
              </button>
            </div>
          </template>

          <!-- 收藏 / 合集：单条同步链路 -->
          <div v-else class="sync-bar" :class="{ on: row.enabled }">
            <span class="sync-label">
              <span class="ui-dot" :class="row.enabled ? 'ok' : ''" />
              {{ row.enabled ? '同步已开启' : '同步未开启' }}
            </span>
            <span class="grow" />
            <button
              class="ui-switch lg"
              :class="{ on: row.enabled }"
              :aria-label="row.enabled ? '关闭同步' : '开启同步'"
              @click="toggleEnabled(row)"
            >
              <i />
            </button>
          </div>

          <p v-if="isCreator(row) && row.signature" class="sig">{{ row.signature }}</p>
          <p v-else-if="!isCreator(row)" class="sig">{{ filterSummary(row) }}</p>

          <div class="foot">
            <span v-if="syncActive(row)" class="ui-toggle">
              <span>全量</span>
              <button class="ui-switch sm" :class="{ on: !row.only_new }" @click="toggleFull(row)">
                <i />
              </button>
            </span>
            <span v-else class="ui-badge">{{ isCreator(row) ? '已跟踪' : '仅收藏' }}</span>
            <span class="grow" />
            <button class="ui-btn ghost sm" :disabled="!syncActive(row)" @click="openRuns(row)">历史</button>
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
            <el-option v-for="o in sourceTypeOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源" v-if="!editing">
          <div style="width: 100%">
            <el-radio-group v-model="subPickMode" size="small" style="margin-bottom: 6px" @change="onPickModeChange">
              <el-radio-button value="list" :disabled="manualSourceOnly">从列表选择</el-radio-button>
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
            <el-input v-else v-model="form.remote_id" :placeholder="manualSourcePlaceholder" />
            <span v-if="manualSourceOnly" class="detail">
              {{ currentFormPlatformLabel }}暂不支持自动发现关注博主，可手动填写主页 ID；保存后再开启该博主的视频同步。
            </span>
          </div>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.title" placeholder="例如：技术收藏夹（可留空）" /></el-form-item>

        <div class="form-sec">同步</div>
        <el-form-item v-if="form.source_type === 'creator'" label="关注视频">
          <el-switch v-model="form.sync_videos" />
          <span class="detail">开启后才会同步该博主名下的视频（默认可先只跟踪关注关系）</span>
        </el-form-item>
        <el-form-item :label="form.source_type === 'creator' ? '跟踪关注关系' : '启用同步'">
          <el-switch v-model="form.enabled" />
          <span v-if="form.source_type === 'creator'" class="detail">关闭＝暂停跟踪这段关注关系</span>
        </el-form-item>
        <el-form-item label="周期">
          <el-select v-model="form.interval_mode" style="width: 180px">
            <el-option label="继承全局随机区间" value="inherit" />
            <el-option label="固定间隔" value="fixed" />
          </el-select>
          <el-input-number v-if="form.interval_mode === 'fixed'" v-model="form.interval_minutes" :min="10" :step="10" style="margin-left: 8px" />
        </el-form-item>
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

        <div class="form-sec">下载</div>
        <el-form-item label="自动下载"><el-switch v-model="form.auto_download" /></el-form-item>
        <el-form-item label="下载画质">
          <el-select v-model="form.download_quality" style="width: 200px" :disabled="!currentQualityOptions.length">
            <el-option v-for="q in currentQualityOptions" :key="q.value" :label="q.label" :value="q.value" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="currentCapability?.comments" label="同步评论">
          <el-radio-group v-model="form.comment_mode">
            <el-radio-button value="off">不抓</el-radio-button>
            <el-radio-button value="gate">热度门控</el-radio-button>
            <el-radio-button value="force">强制抓取</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <div class="form-sec">加工</div>
        <el-form-item label="自动转录">
          <el-select v-model="form.auto_transcribe" style="width: 180px">
            <el-option label="继承全局" :value="null" />
            <el-option label="开启" :value="true" />
            <el-option label="关闭" :value="false" />
          </el-select>
          <span class="detail">未单独设置时跟随「设置 → 视频理解」的全局默认</span>
        </el-form-item>
        <el-form-item label="自动生成速览">
          <el-select v-model="form.auto_analyze" style="width: 180px">
            <el-option label="继承全局" :value="null" />
            <el-option label="开启" :value="true" />
            <el-option label="关闭" :value="false" />
          </el-select>
          <span class="detail">转录后自动生成摘要 + 标签；未单独设置时跟随全局默认</span>
        </el-form-item>

        <div class="form-sec">存储</div>
        <el-form-item label="根目录">
          <el-select v-model="form.storage_mode" style="width: 200px">
            <el-option label="继承账号目录" value="account" />
            <el-option label="指定根目录" value="custom" />
          </el-select>
          <el-select
            v-if="form.storage_mode === 'custom'"
            v-model="form.storage_root_id"
            placeholder="选择存储根"
            style="width: 200px; margin-left: 8px"
          >
            <el-option v-for="r in storageRoots" :key="r.id" :label="`${r.name}（${r.path}）`" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="子目录模板">
          <el-input v-model="form.subdir_template" placeholder="{platform}/{account}/{creator}/{date}" />
          <span class="detail">可用占位符：{platform} {account} {creator} {date} {content_id}</span>
        </el-form-item>
        <el-form-item label="路径预览">
          <div class="path-preview">
            <code>{{ storagePreview }}</code>
          </div>
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
        <el-table-column prop="updated" label="全量刷新" width="90" />
        <el-table-column label="错误">
          <template #default="{ row }">{{ row.error || '—' }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { computed, onMounted, reactive, ref } from 'vue'
import Avatar from '../components/Avatar.vue'
import PlatformMark from '../components/PlatformMark.vue'
import { api, type Account, type PlatformInfo } from '../api'
import { safeExternalUrl } from '../utils/safeUrl'
import { parseUtc } from '../utils/time'

/** 统一后的"我跟踪的来源"：博主 = creator 来源，收藏夹/合集 = 其他来源。 */
interface TrackRow {
  id: number
  account_id: number
  platform: string
  source_type: string
  remote_id: string
  title: string
  enabled: boolean
  // creator 专用二级开关：是否同步该博主名下视频
  sync_videos: boolean
  auto_download: boolean
  interval_minutes: number | null
  subdir_template: string
  storage_root_id: number | null
  min_play: number
  publish_after_days: number
  max_items: number
  only_new: boolean
  download_quality: string
  comment_mode: string
  auto_transcribe: boolean | null
  auto_analyze: boolean | null
  last_sync_at: string | null
  creator_avatar: string
  avatar: string
  avatar_url: string
  // creator 专属（由后端从 creator_profile 合成）
  author_name?: string
  home_url?: string
  signature?: string
  verified?: boolean
  follower_count?: number
}

interface RemoteSource { remote_id: string; title: string }
interface SyncRunRow {
  id: number; job_id: number | null; started_at: string | null; status: string
  fetched: number; added: number; updated: number; error: string
}

const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音', bilibili: 'B站', xhs: '小红书', zhihu: '知乎',
}
const SUPPORTED = ['douyin', 'bilibili', 'xhs', 'zhihu']
const SOURCE_TYPES: Record<string, string> = {
  favorite: '收藏', collect_folder: '收藏夹', collection: '合集',
  series: '短剧', creator: '博主',
}
// 每种平台的来源类型选项（抽屉表单里只显示该平台真正支持的）
const DEFAULT_SUBDIR = '{platform}/{account}/{creator}/{date}'

const platformInfo = ref<PlatformInfo[]>([])
const currentCapability = computed(() => platformInfo.value.find((p) => p.platform === currentFormPlatform.value))
const sourceTypeOptions = computed(() => {
  const cap = platformInfo.value.find((p) => p.platform === currentFormPlatform.value)
  return cap?.source_types || []
})
const currentQualityOptions = computed(() => currentCapability.value?.download_qualities || [])

const accounts = ref<Account[]>([])
const rows = ref<TrackRow[]>([])
const loading = ref(false)
const refreshing = ref(false)
const cooling = ref(false)
const activePlatform = ref('douyin')
const activeAccountId = ref('')
const activeType = ref<'all' | 'creator' | 'favorite' | 'other'>('all')
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
  sync_videos: false,
  auto_download: false,
  interval_mode: 'inherit' as 'inherit' | 'fixed',
  interval_minutes: 360,
  subdir_template: DEFAULT_SUBDIR,
  min_play: 0,
  publish_after_days: 0,
  max_items: 50,
  only_new: true,
  download_quality: 'best',
  comment_mode: 'gate',
  auto_transcribe: null as boolean | null,
  auto_analyze: null as boolean | null,
  // 存储：账户级根目录（默认继承账号）或指定根 + 子目录模板
  storage_mode: 'account' as 'account' | 'custom',
  storage_root_id: null as number | null,
})

const storageRoots = ref<Array<{ id: number; name: string; path: string }>>([])

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
const currentFormPlatform = computed(() =>
  accounts.value.find((a) => a.id === form.account_id)?.platform || activePlatform.value,
)
const currentFormPlatformLabel = computed(() => PLATFORM_LABELS[currentFormPlatform.value] || currentFormPlatform.value)
const manualSourceOnly = computed(() => !(currentCapability.value?.follow_import ?? false))
const canImportFollowings = computed(() =>
  Boolean(currentAccount.value && platformInfo.value.find((p) => p.platform === currentAccount.value?.platform)?.follow_import),
)
const manualSourcePlaceholder = computed(() => {
  if (currentFormPlatform.value === 'xhs') return '小红书博主 user_id（主页链接 /user/profile/ 后的 ID）'
  if (currentFormPlatform.value === 'zhihu') return '知乎博主 url_token（主页链接 /people/ 后的 ID）'
  return '收藏夹ID / 合集ID / UP主mid / 博主sec_uid'
})

/** 表单当前账号：新建时按 form.account_id，编辑时按行归属。 */
const formAccount = computed(() =>
  accounts.value.find((a) => a.id === (editing.value ? editing.value.account_id : form.account_id)),
)

/** 路径预览：根目录 + 模板渲染结果，让用户看到文件实际落点。 */
const storagePreview = computed(() => {
  let base: string
  if (form.storage_mode === 'custom' && form.storage_root_id) {
    base = storageRoots.value.find((r) => r.id === form.storage_root_id)?.path || '<未选择根目录>'
  } else {
    const acc = formAccount.value as any
    base = acc?.storage_root_path || '<账号默认目录>'
  }
  const acc = formAccount.value as any
  const segs = {
    platform: acc?.platform || 'douyin',
    account: acc?.display_name || (acc ? `account-${acc.id}` : '账号'),
    creator: '<博主>',
    date: new Date().toISOString().slice(0, 10),
    content_id: '<内容ID>',
  }
  let rel = form.subdir_template || ''
  for (const [k, v] of Object.entries(segs)) rel = rel.replaceAll(`{${k}}`, v)
  rel = rel.replace(/[{}]/g, '').replace(/\/+/g, '/').replace(/^\/+|\/+$/g, '')
  const join = base.endsWith('/') || base.endsWith('\\') ? base : `${base}/`
  return `${join}${rel}`.replace(/([^:])\/{2,}/g, '$1/')
})

/** 当前账号下的行 */
const accountRows = computed(() =>
  rows.value.filter((r) => String(r.account_id) === activeAccountId.value),
)
const visibleRows = computed(() => {
  const t = activeType.value
  if (t === 'all') return accountRows.value
  return accountRows.value.filter((r) => matchType(r, t))
})
function isCreator(row: TrackRow) { return row.source_type === 'creator' }
/** 收藏夹/合集没有头像，用一个几何符号代替，保证卡片结构一致。 */
function sourceGlyph(t: string) {
  return ({ favorite: '☆', collection: '▤', like: '♥', collect_folder: '❏', series: '▧' } as Record<string, string>)[t] ?? '▦'
}
const enabledCount = computed(() => accountRows.value.filter((r) => syncActive(r)).length)

const typeFilters = [
  { value: 'all' as const, label: '全部' },
  { value: 'creator' as const, label: '博主' },
  { value: 'favorite' as const, label: '收藏' },
  { value: 'other' as const, label: '收藏夹 / 合集' },
]
function typeCount(v: 'creator' | 'favorite' | 'other') {
  return accountRows.value.filter((r) => matchType(r, v)).length
}
function matchType(r: TrackRow, v: string) {
  if (v === 'creator') return r.source_type === 'creator'
  if (v === 'favorite') return r.source_type === 'favorite'
  if (v === 'other') return !['creator', 'favorite'].includes(r.source_type)
  return true
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
  const [accountRows, roots, platforms] = await Promise.all([
    api.get('/api/accounts'),
    api.get('/api/system/storage-roots'),
    api.get('/api/platforms'),
  ])
  accounts.value = accountRows.data
  storageRoots.value = roots.data
  platformInfo.value = platforms.data
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
function switchAccount(id: string) {
  activeAccountId.value = id
}

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
      // 只改「关注关系」这一个开关，不能顺手改动 sync_videos。
      await api.put(`/api/followed-creators/${row.id}`, { sync_enabled: next })
    } else {
      await api.patch(`/api/subscriptions/${row.id}`, { enabled: next })
    }
    ElMessage.success(next ? '同步已开启' : '同步已关闭')
  } catch (e: any) {
    row.enabled = !next
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

/** 博主卡片上的二级开关：是否同步该博主名下视频。 */
async function toggleVideos(row: TrackRow) {
  const next = !row.sync_videos
  row.sync_videos = next
  if (next) row.enabled = true   // 开后端会一并打开关注关系，前端保持同步
  try {
    await api.put(`/api/followed-creators/${row.id}`, { sync_videos: next })
  } catch (e: any) {
    row.sync_videos = !next
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

/** 该行现在是否会真的去同步内容（决定"全量/历史"是否有意义）。 */
function syncActive(row: TrackRow) {
  return isCreator(row) ? (row.enabled && row.sync_videos) : row.enabled
}

async function toggleFull(row: TrackRow) {
  const nextFull = row.only_new      // 当前是"只新"→ 打开全量
  row.only_new = !nextFull
  try {
    // 按来源类型走各自的写入口：creator 用关注博主接口，收藏夹/合集用订阅接口。
    // 之前这里硬编码 followed-creators，收藏夹/合集会 404「关注博主不存在」。
    if (row.source_type === 'creator') {
      await api.put(`/api/followed-creators/${row.id}`, { full_sync: nextFull })
    } else {
      await api.patch(`/api/subscriptions/${row.id}`, { only_new: !nextFull })
    }
    ElMessage.success(nextFull ? '已切换为全量同步' : '已切换为仅同步新内容')
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
    ElMessage.success(`已提交同步任务 #${data.job_id}`)
    const run = await waitForSyncJob(row, data.job_id)
    if (run?.status === 'success') {
      const upd = run.updated ? `，全量刷新 ${run.updated} 条` : ''
      ElMessage.success(`同步完成：抓取 ${run.fetched} 条，新增 ${run.added} 条${upd}`)
    } else if (run?.status === 'error') {
      ElMessage.error(run.error || '同步失败')
    }
    await load()
  } finally {
    syncing.value = null
  }
}

async function waitForSyncJob(row: TrackRow, jobId: number): Promise<SyncRunRow | null> {
  const deadline = Date.now() + 10 * 60 * 1000
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2000))
    const { data: job } = await api.get(`/api/pipeline/jobs/${jobId}`)
    if (job.status === 'error') {
      return { id: 0, job_id: jobId, started_at: null, status: 'error', fetched: 0, added: 0, updated: 0, error: job.error }
    }
    if (job.run_id) {
      const { data: run } = await api.get(`/api/subscriptions/${row.id}/runs/${job.run_id}`)
      if (run.status !== 'running') return run
    }
    if (job.status === 'done') {
      return { id: 0, job_id: jobId, started_at: null, status: 'success', fetched: 0, added: 0, updated: 0, error: '' }
    }
  }
  ElMessage.warning('同步仍在后台执行，可在同步历史中查看结果')
  return null
}

function openHome(row: TrackRow) {
  const target = safeExternalUrl(row.home_url)
  if (target) window.open(target, '_blank', 'noopener')
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
  if (manualSourceOnly.value) {
    form.source_type = 'creator'
    subPickMode.value = 'manual'
  }
  if (currentCapability.value && currentQualityOptions.value.length
      && !currentQualityOptions.value.some((q) => q.value === form.download_quality)) {
    form.download_quality = currentQualityOptions.value[0].value
  }
  dialog.value = true
}

function openEdit(row: TrackRow) {
  editing.value = row
  Object.assign(form, {
    account_id: row.account_id, source_type: row.source_type, remote_id: row.remote_id,
    title: row.title, enabled: row.enabled, sync_videos: row.sync_videos,
    auto_download: row.auto_download,
    interval_mode: row.interval_minutes == null ? 'inherit' : 'fixed',
    interval_minutes: row.interval_minutes ?? 360, subdir_template: row.subdir_template,
    storage_root_id: row.storage_root_id ?? null,
    min_play: row.min_play, publish_after_days: row.publish_after_days,
    max_items: row.max_items, only_new: row.only_new,
    download_quality: row.download_quality, comment_mode: row.comment_mode,
    auto_transcribe: row.auto_transcribe, auto_analyze: row.auto_analyze,
    // 回显该来源已指定的存储根；未指定则继承账号目录（账号绑定在账号中心管理）。
    storage_mode: row.storage_root_id ? 'custom' : 'account',
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

function resetForm() {
  Object.assign(form, {
    account_id: null, source_type: 'favorite', remote_id: '', title: '',
    enabled: true, sync_videos: false, auto_download: false, interval_mode: 'inherit', interval_minutes: 360,
    subdir_template: DEFAULT_SUBDIR, min_play: 0, publish_after_days: 0,
    max_items: 50, only_new: true, download_quality: 'best', comment_mode: 'gate',
    auto_transcribe: null, auto_analyze: null,
    storage_mode: 'account', storage_root_id: null,
  })
  sources.value = []
  sourcesError.value = ''
  subPickMode.value = 'list'
}

function onPickModeChange() { sources.value = []; sourcesError.value = '' }

function onFormAccountChange() {
  sources.value = []
  // 切账号后把不支持的来源类型回退到该平台的第一项
  const allowed = sourceTypeOptions.value.map((o) => o.value)
  if (!allowed.includes(form.source_type)) {
    form.source_type = allowed[0] || ''
    form.remote_id = ''
  }
  const qualities = currentQualityOptions.value.map((q) => q.value)
  if (qualities.length && !qualities.includes(form.download_quality)) {
    form.download_quality = qualities[0]
  }
  subPickMode.value = manualSourceOnly.value ? 'manual' : 'list'
  if (subPickMode.value === 'list') loadSources()
}
function onSourceTypeChange() {
  form.remote_id = ''
  sources.value = []
  form.comment_mode = 'gate'
  if (manualSourceOnly.value) subPickMode.value = 'manual'
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
  if (form.storage_mode === 'custom' && !form.storage_root_id) {
    return ElMessage.warning('请选择要指定的存储根目录，或改为继承账号目录')
  }
  submitting.value = true
  try {
    // 存储根：继承账号 -> null（后端回退账号绑定/默认根）；指定 -> 具体 root id。
    const rootOverride = form.storage_mode === 'custom' ? form.storage_root_id : null
    const common = {
      title: form.title, enabled: form.enabled, sync_videos: form.sync_videos,
      auto_download: form.auto_download,
      interval_minutes: form.interval_mode === 'inherit' ? null : form.interval_minutes,
      subdir_template: form.subdir_template,
      storage_root_id: rootOverride,
      min_play: form.min_play, publish_after_days: form.publish_after_days,
      max_items: form.max_items, only_new: form.only_new,
      download_quality: form.download_quality, comment_mode: form.comment_mode,
      auto_transcribe: form.auto_transcribe, auto_analyze: form.auto_analyze,
    }
    if (editing.value) {
      await api.patch(`/api/subscriptions/${editing.value.id}`, common)
    } else {
      await api.post('/api/subscriptions', {
        account_id: form.account_id, source_type: form.source_type, remote_id: form.remote_id,
        ...common,
      })
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
  const d = parseUtc(v)
  if (!d) return '—'
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(async () => {
  await loadAccounts()
  await load()
})
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }

/* 账号级同步总开关已移到「账号中心」；本页每条订阅仍各自有开关。 */
.ui-switch[disabled] { opacity: .45; cursor: not-allowed; }

/* 博主卡片：关注关系是次级信息，独立一行 */
.relation-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 4px 2px; font-size: var(--fs-sm); color: var(--muted);
}
.hint { font-size: var(--fs-sm); color: var(--muted); }
.grid-wrap { display: flex; flex-direction: column; gap: 16px; }
.ui-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; }
.ui-card {
  display: flex; flex-direction: column; gap: 8px;
  /* 卡片边界线：与账号卡片同款硬边 + 偏移阴影，看清每张卡的边界 */
  background: var(--surface);
  border: 1px solid var(--ink);
  box-shadow: var(--shadow-1);
  padding: var(--sp-4);
  transition: transform .14s ease-out, box-shadow .14s ease-out, border-color .14s ease-out;
}
.ui-card:hover { transform: translate(-1px, -1px); box-shadow: var(--shadow-2); }
.ui-card.off { opacity: .72; border-style: dashed; }
.ui-card.off:hover { opacity: 1; }
.ui-card .row { display: flex; align-items: center; gap: 10px; }

/* 头部同步开关条：让"开/关同步"成为一眼可见的主操作 */
.sync-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 10px;
  border: 1px solid var(--line-soft);
  background: var(--surface-2);
  transition: background .14s ease-out, border-color .14s ease-out;
}
.sync-bar.on { border-color: color-mix(in oklab, var(--ok) 40%, var(--ink)); background: var(--ok-soft); }
.sync-bar .sync-label {
  display: inline-flex; align-items: center; gap: 7px;
  font-size: var(--fs-sm); font-weight: 650; color: var(--ink-2);
}
.sync-bar.on .sync-label { color: var(--ink); }
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

/* 设置弹窗分节标题 */
.form-sec {
  margin: 6px 0 10px; padding-left: 9px;
  font-size: var(--fs-md); font-weight: 700; color: var(--ink);
  border-left: 4px solid var(--accent);
}
.form-sec:first-of-type { margin-top: 0; }
.path-preview {
  width: 100%; padding: 7px 10px; overflow-x: auto;
  background: var(--surface-2); border: 1px solid var(--line-soft);
}
.path-preview code { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--ink-2); white-space: nowrap; }
</style>
