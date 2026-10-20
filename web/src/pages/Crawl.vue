<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>关键词采集</h1>
      <span class="sub">按关键词发现作品 · 评论策略由任务显式控制</span>
    </div>

    <div class="toolbar">
      <button class="ui-btn primary" @click="openCreate">新建采集任务</button>
      <button class="ui-btn" :disabled="loading" @click="load">刷新</button>
      <span class="grow" />
      <span class="ui-badge">任务 {{ tasks.length }}</span>
      <span class="ui-badge ok">启用 {{ tasks.filter((t) => t.enabled).length }}</span>
    </div>
    <p class="hint">
      关键词采集走平台原生适配器，需先在「账号中心」完成对应平台登录；采集结果进素材库，
      评论可选不抓、按热度门控或强制抓取；未开启评论的平台不会展示该选项。
    </p>

    <div v-loading="loading">
      <div v-if="!loading && !tasks.length" class="ui-empty">
        <div class="big">还没有采集任务</div>
        <p>点「新建采集任务」，填一个关键词，选平台和周期即可开始。</p>
      </div>
      <div v-else class="task-grid">
        <article v-for="row in tasks" :key="row.id" class="task" :class="{ off: !row.enabled }">
          <div class="task-top">
            <PlatformMark :platform="row.platform" size="md" />
            <span class="ui-badge">{{ row.sort === 'popular' ? '热门优先' : '最新优先' }}</span>
            <span class="grow" />
            <button class="ui-switch" :class="{ on: row.enabled }" @click="toggle(row, !row.enabled)">
              <i />
            </button>
          </div>
          <div class="kw" :title="row.keyword">{{ row.keyword }}</div>
          <dl class="task-meta">
            <div><dt>单次上限</dt><dd class="num">{{ row.limit_per_run }} 条</dd></div>
            <div><dt>周期</dt><dd>{{ intervalText(row.interval_minutes) }}</dd></div>
            <div><dt>最低播放</dt><dd class="num">{{ row.min_play > 0 ? compact(row.min_play) : '不限' }}</dd></div>
            <div><dt>上次入队</dt><dd class="num">{{ fmt(row.last_attempt_at || row.last_run_at) }}</dd></div>
          </dl>
          <div class="task-tags">
            <span class="ui-badge" :class="{ warn: row.comment_mode !== 'off' }">
              {{ commentModeText(row.comment_mode) }}
            </span>
            <span class="ui-badge" :class="{ acc: row.to_candidate }">
              {{ row.to_candidate ? '进候选池' : '仅入库' }}
            </span>
          </div>
          <div class="task-acts">
            <button class="ui-btn sm" :disabled="running === row.id" @click="run(row)">
              {{ running === row.id ? '已入队…' : '立即运行' }}
            </button>
            <button class="ui-btn sm" @click="openRuns(row)">运行记录</button>
            <button class="ui-btn sm" @click="openEdit(row)">编辑</button>
            <button class="ui-btn sm danger" @click="confirmRemove(row)">删除</button>
          </div>
        </article>
      </div>
    </div>

    <!-- 删除确认 -->
    <div v-if="removeTarget" class="ui-modal" @click.self="removeTarget = null">
      <div class="ui-modal-box narrow">
        <div class="ui-panel-head"><h2>删除采集任务</h2></div>
        <div class="ui-modal-body">
          <p>确定删除关键词「{{ removeTarget.keyword }}」的采集任务？运行记录会一并删除。</p>
        </div>
        <div class="ui-modal-foot">
          <button class="ui-btn" @click="removeTarget = null">取消</button>
          <button class="ui-btn danger" @click="doRemove">确认删除</button>
        </div>
      </div>
    </div>

    <el-dialog v-model="dialog" :title="editing ? '编辑任务' : '新建采集任务'" width="520px" destroy-on-close>
      <el-form label-width="100px">
        <el-form-item label="平台">
          <el-select v-model="form.platform">
            <el-option v-for="p in platforms" :key="p.value" :label="p.label" :value="p.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="采集账号">
          <el-select v-model="form.account_id" clearable placeholder="自动轮换（推荐）" style="width: 100%">
            <el-option
              v-for="a in formAccounts"
              :key="a.id"
              :label="a.display_name || `${a.platform} #${a.id}`"
              :value="a.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="form.keyword" placeholder="例如：露营装备" />
        </el-form-item>
        <el-form-item label="排序">
          <el-radio-group v-model="form.sort">
            <el-radio-button value="popular">热门</el-radio-button>
            <el-radio-button value="latest">最新</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="单次上限">
          <el-input-number v-model="form.limit_per_run" :min="1" :max="500" />
        </el-form-item>
        <el-form-item label="最低播放量">
          <el-input-number v-model="form.min_play" :min="0" :step="1000" />
        </el-form-item>
        <el-form-item label="采集周期">
          <el-select v-model="form.interval_minutes">
            <el-option label="每小时" :value="60" />
            <el-option label="每 6 小时" :value="360" />
            <el-option label="每天" :value="1440" />
            <el-option label="每周" :value="10080" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="currentCapability?.comments" label="评论">
          <el-radio-group v-model="form.comment_mode">
            <el-radio-button value="off">不抓</el-radio-button>
            <el-radio-button value="gate">热度门控</el-radio-button>
            <el-radio-button value="force">强制抓取</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="视频进候选池">
          <el-switch v-model="form.to_candidate" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="runsDrawer" title="运行记录" size="50%">
      <el-table :data="runs" v-loading="runsLoading">
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'success' ? 'success' : row.status === 'error' ? 'danger' : 'warning'">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fetched" label="抓取" width="80" />
        <el-table-column prop="added" label="新增" width="80" />
        <el-table-column label="使用账号" width="130">
          <template #default="{ row }">{{ accountLabel(row.account_id) }}</template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="160" show-overflow-tooltip />
        <el-table-column label="开始时间" width="160">
          <template #default="{ row }">{{ fmt(row.started_at) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { computed, onMounted, reactive, ref } from 'vue'
import PlatformMark from '../components/PlatformMark.vue'
import { api, type PlatformInfo } from '../api'
import { parseUtc } from '../utils/time'

const platformInfo = ref<PlatformInfo[]>([])
const platforms = computed(() => platformInfo.value
  .filter((p) => p.keyword_crawl)
  .map((p) => ({ value: p.platform, label: p.label })))
const currentCapability = computed(() => platformInfo.value.find((p) => p.platform === form.platform))

interface CrawlTask {
  id: number
  platform: string
  account_id: number | null
  keyword: string
  sort: string
  limit_per_run: number
  comment_mode: string
  to_candidate: boolean
  min_play: number
  interval_minutes: number
  enabled: boolean
  last_run_at: string | null
  last_attempt_at: string | null
}

const tasks = ref<CrawlTask[]>([])
const accounts = ref<Array<{ id: number; platform: string; display_name: string }>>([])
const formAccounts = computed(() =>
  accounts.value.filter((a) => a.platform === form.platform),
)
const loading = ref(false)
const running = ref<number | null>(null)
const dialog = ref(false)
const submitting = ref(false)
const editing = ref<CrawlTask | null>(null)
const runsDrawer = ref(false)
const runs = ref<any[]>([])
const runsLoading = ref(false)

const form = reactive({
  platform: 'xhs',
  account_id: null as number | null,
  keyword: '',
  sort: 'popular',
  limit_per_run: 50,
  comment_mode: 'off',
  to_candidate: true,
  min_play: 0,
  interval_minutes: 1440,
  enabled: true,
})

function platformLabel(v: string) {
  return platforms.find((p) => p.value === v)?.label ?? v
}
function accountLabel(accountId: number | null) {
  if (!accountId) return '未指定'
  return accounts.value.find((a) => a.id === accountId)?.display_name || `#${accountId}`
}
function intervalText(m: number) {
  const known: Record<number, string> = {
    60: '每小时', 120: '每 2 小时', 180: '每 3 小时', 360: '每 6 小时',
    720: '每 12 小时', 1440: '每天', 2880: '每 2 天', 4320: '每 3 天', 10080: '每周',
  }
  if (known[m]) return known[m]
  if (m % 1440 === 0) return `每 ${m / 1440} 天`
  if (m % 60 === 0) return `每 ${m / 60} 小时`
  return `每 ${m} 分钟`
}
function commentModeText(mode: string) {
  return ({ off: '不抓评论', gate: '评论门控', force: '强制抓评论' } as Record<string, string>)[mode] || mode
}
function fmt(v: string | null) {
  if (!v) return '—'
  return parseUtc(v)?.toLocaleString('zh-CN', { hour12: false }) ?? '—'
}
function compact(n: number) {
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`
  return String(n)
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/api/crawl-tasks')
    tasks.value = data
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  const first = platforms.value[0]?.value || 'xhs'
  Object.assign(form, {
    platform: first, account_id: null, keyword: '', sort: 'popular', limit_per_run: 50,
    comment_mode: 'off', to_candidate: true, min_play: 0, interval_minutes: 1440, enabled: true,
  })
  dialog.value = true
}

async function submit() {
  if (!form.keyword.trim()) {
    ElMessage.warning('请填写关键词')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      await api.put(`/api/crawl-tasks/${editing.value.id}`, form)
    } else {
      await api.post('/api/crawl-tasks', form)
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

async function toggle(row: CrawlTask) {
  // 先落库再改 UI，避免请求失败后界面与后端不一致
  const next = !row.enabled
  try {
    await api.put(`/api/crawl-tasks/${row.id}`, {
      platform: row.platform,
      account_id: row.account_id,
      keyword: row.keyword,
      sort: row.sort,
      limit_per_run: row.limit_per_run,
      comment_mode: row.comment_mode,
      to_candidate: row.to_candidate,
      min_play: row.min_play,
      interval_minutes: row.interval_minutes,
      enabled: next,
    })
    row.enabled = next
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

async function run(row: CrawlTask) {
  running.value = row.id
  try {
    const { data } = await api.post(`/api/crawl-tasks/${row.id}/run`)
    ElMessage.success(`已加入采集队列 #${data.job_id}`)
    const deadline = Date.now() + 10 * 60 * 1000
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 2000))
      const { data: job } = await api.get(`/api/pipeline/jobs/${data.job_id}`)
      if (job.status === 'error') {
        ElMessage.error(job.error || '采集失败')
        break
      }
      if (job.status === 'done') {
        ElMessage.success('采集完成')
        await load()
        break
      }
    }
  } finally {
    running.value = null
  }
}

const removeTarget = ref<CrawlTask | null>(null)

function confirmRemove(row: CrawlTask) {
  removeTarget.value = row
}

async function doRemove() {
  if (!removeTarget.value) return
  await api.delete(`/api/crawl-tasks/${removeTarget.value.id}`)
  removeTarget.value = null
  ElMessage.success('已删除')
  await load()
}

function openEdit(row: CrawlTask) {
  editing.value = row
  Object.assign(form, {
    platform: row.platform,
    account_id: row.account_id,
    keyword: row.keyword,
    sort: row.sort,
    limit_per_run: row.limit_per_run,
    comment_mode: row.comment_mode,
    to_candidate: row.to_candidate,
    min_play: row.min_play,
    interval_minutes: row.interval_minutes,
    enabled: row.enabled,
  })
  dialog.value = true
}

async function openRuns(row: CrawlTask) {
  runsDrawer.value = true
  runsLoading.value = true
  try {
    const { data } = await api.get(`/api/crawl-tasks/${row.id}/runs`)
    runs.value = data
  } finally {
    runsLoading.value = false
  }
}

async function loadAccounts() {
  const { data } = await api.get('/api/accounts')
  accounts.value = data
}

onMounted(async () => {
  platformInfo.value = (await api.get('/api/platforms')).data
  await loadAccounts()
  await load()
})
</script>

<style scoped>
.toolbar { display: flex; gap: var(--sp-3); align-items: center; flex-wrap: wrap; }
.toolbar .grow { margin-left: auto; }
.hint { color: var(--muted); font-size: var(--fs-sm); margin: 0; line-height: 1.6; }

/* 采集任务卡片 */
.task-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--sp-3);
}
.task {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: var(--sp-4);
  background: var(--surface);
  border: 1px solid var(--ink);
  box-shadow: var(--shadow-1);
  transition: transform .14s ease-out, box-shadow .14s ease-out;
}
.task:hover { transform: translate(-1px, -1px); box-shadow: var(--shadow-2); }
.task.off { opacity: .62; border-style: dashed; box-shadow: none; }
.task-top { display: flex; align-items: center; gap: 8px; }
.task-top .grow { margin-left: auto; }
.task .plat {
  font-size: var(--fs-sm); font-weight: 700; letter-spacing: .04em;
  padding: 1px 7px; background: var(--ink); color: var(--surface);
}
.task .kw {
  font-size: var(--fs-lg); font-weight: 700; color: var(--ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.task-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin: 0; }
.task-meta > div { display: flex; justify-content: space-between; gap: 8px; font-size: var(--fs-xs); }
.task-meta dt { color: var(--muted); margin: 0; }
.task-meta dd { color: var(--ink-2); margin: 0; font-variant-numeric: tabular-nums; }
.task-tags { display: flex; gap: 6px; flex-wrap: wrap; }
.task-acts {
  display: flex; gap: 6px; flex-wrap: wrap;
  margin-top: auto; padding-top: 8px; border-top: 1px solid var(--line-soft);
}

/* 删除确认弹层 */
.ui-modal {
  position: fixed; inset: 0; z-index: 200;
  background: color-mix(in oklab, var(--ink) 45%, transparent);
  display: grid; place-items: center; padding: var(--sp-5);
}
.ui-modal-box { background: var(--surface); border: 2px solid var(--ink); box-shadow: var(--shadow-2); width: min(480px, 100%); }
.ui-modal-body { padding: var(--sp-5); color: var(--ink-2); }
.ui-modal-body p { margin: 0; }
.ui-modal-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: var(--sp-4) var(--sp-5); border-top: 1px solid var(--line-soft);
}
</style>
