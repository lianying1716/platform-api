<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" :loading="refreshing" :disabled="!currentAccount" @click="refresh">
        刷新关注列表
      </el-button>
      <span class="hint">
        头像/昵称来自平台真实数据，点击头像或昵称可跳转到博主主页。
      </span>
    </div>

    <!-- 一级 tab：平台 -->
    <el-tabs v-model="activePlatform" class="platform-tabs" @tab-change="onPlatformChange">
      <el-tab-pane v-for="p in platformsWithAccounts" :key="p.platform" :name="p.platform">
        <template #label>
          <span>{{ p.label }}</span>
          <el-tag v-if="countFor(p.platform)" size="small" effect="plain" class="tab-count">
            {{ countFor(p.platform) }}
          </el-tag>
        </template>
      </el-tab-pane>
    </el-tabs>

    <!-- 二级 tab：账号 -->
    <el-tabs v-if="accountsForPlatform.length > 1" v-model="activeAccountId" type="card"
      @tab-change="onAccountChange">
      <el-tab-pane v-for="a in accountsForPlatform" :key="a.id" :name="String(a.id)"
        :label="a.display_name || a.platform_label" />
    </el-tabs>
    <div v-else-if="accountsForPlatform.length === 1" class="single-account">
      账号：{{ accountsForPlatform[0].display_name || accountsForPlatform[0].platform_label }}
    </div>

    <div v-loading="loading">
      <el-empty v-if="!loading && !creators.length" :description="emptyText" />
      <div v-else class="grid">
        <el-card v-for="c in creators" :key="c.id" class="creator-card" shadow="hover">
          <div class="card-head">
            <el-avatar :size="64" :src="c.avatar" class="avatar" @click="openHome(c)">
              {{ (c.author_name || '?').slice(0, 1) }}
            </el-avatar>
            <div class="meta">
              <div class="name-row">
                <a v-if="c.home_url" class="name" :href="c.home_url" target="_blank" rel="noopener">
                  {{ c.author_name || c.author_id }}
                </a>
                <span v-else class="name">{{ c.author_name || c.author_id }}</span>
                <el-tag v-if="c.verified" size="small" type="warning" effect="plain">认证</el-tag>
              </div>
              <div class="stats">
                <span v-if="c.follower_count > 0">粉丝 {{ compact(c.follower_count) }}</span>
                <span v-if="c.like_count > 0">获赞 {{ compact(c.like_count) }}</span>
                <span v-if="!c.follower_count && !c.like_count" class="muted">—</span>
              </div>
            </div>
          </div>
          <div v-if="c.signature" class="signature">{{ c.signature }}</div>

          <div class="card-actions">
            <div class="switch-row">
              <span>开启同步</span>
              <el-switch :model-value="c.sync_enabled" :loading="busy === c.id" @change="toggle(c, 'sync_enabled', $event)" />
            </div>
            <!-- 开启同步后才有全量开关 -->
            <div v-if="c.sync_enabled" class="switch-row">
              <span>全量同步</span>
              <el-switch :model-value="c.full_sync" :loading="busy === c.id" @change="toggle(c, 'full_sync', $event)" />
              <el-tooltip :content="c.full_sync ? '全量：覆盖刷新该博主的历史内容' : '关闭时只同步新发布的内容'">
                <el-icon class="help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
          </div>

          <div class="card-foot">
            <el-button size="small" :disabled="!c.sync_enabled" :loading="syncing === c.id" @click="syncNow(c)">
              立即同步
            </el-button>
            <span class="last-sync">{{ c.last_synced_at ? `上次 ${fmt(c.last_synced_at)}` : '未同步' }}</span>
          </div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { api, type Account } from '../api'

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
const creators = ref<FollowedCreator[]>([])
const loading = ref(false)
const refreshing = ref(false)
const busy = ref<number | null>(null)
const syncing = ref<number | null>(null)
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

function countFor(platform: string) {
  return creators.value.filter((c) => c.platform === platform).length
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
  if (!currentAccount.value) {
    creators.value = []
    return
  }
  loading.value = true
  try {
    creators.value = (
      await api.get('/api/followed-creators', {
        params: { platform: activePlatform.value, account_id: currentAccount.value.id },
      })
    ).data
  } finally {
    loading.value = false
  }
}

async function onPlatformChange() {
  activeAccountId.value = ''
  pickAccount()
  await load()
}

async function onAccountChange() {
  await load()
}

async function refresh() {
  if (!currentAccount.value) return
  refreshing.value = true
  try {
    const { data } = await api.post('/api/followed-creators/refresh', {
      account_id: currentAccount.value.id,
    })
    ElMessage.success(`已更新 ${data.count} 位关注博主`)
    await load()
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

onMounted(async () => {
  await loadAccounts()
  await load()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}
.hint {
  font-size: 12px;
  color: #909399;
}
.platform-tabs {
  margin-bottom: 4px;
}
.tab-count {
  margin-left: 6px;
}
.single-account {
  font-size: 13px;
  color: #606266;
  margin: 4px 0 12px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
  margin-top: 12px;
}
.creator-card {
  display: flex;
  flex-direction: column;
}
.card-head {
  display: flex;
  gap: 12px;
  align-items: center;
}
.avatar {
  cursor: pointer;
  flex-shrink: 0;
}
.meta {
  min-width: 0;
}
.name-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.name {
  font-weight: 600;
  font-size: 15px;
  color: inherit;
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.name:hover {
  color: #409eff;
  text-decoration: underline;
}
.stats {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  display: flex;
  gap: 10px;
}
.muted {
  color: #c0c4cc;
}
.signature {
  font-size: 12px;
  color: #606266;
  margin-top: 10px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-actions {
  margin-top: 12px;
  border-top: 1px solid #f0f0f0;
  padding-top: 10px;
}
.switch-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  margin: 4px 0;
}
.help {
  color: #c0c4cc;
  cursor: help;
}
.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}
.last-sync {
  font-size: 12px;
  color: #a8abb2;
}
</style>
