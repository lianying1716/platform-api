<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>AI 接入</h1>
      <span class="sub">API Key、MCP 与 Skill 能力开放</span>
    </div>

    <div class="ui-tabs">
      <button class="ui-tab" :class="{ on: tab === 'keys' }" @click="tab = 'keys'">API Key</button>
      <button class="ui-tab" :class="{ on: tab === 'mcp' }" @click="tab = 'mcp'">MCP 接入</button>
      <button class="ui-tab" :class="{ on: tab === 'logs' }" @click="switchLogs">调用日志</button>
    </div>

    <!-- API Key 管理 -->
    <template v-if="tab === 'keys'">
      <div class="bar">
        <button class="ui-btn primary" @click="dialog = true">创建 Key</button>
        <span class="grow" />
        <span class="ui-badge">共 {{ keys.length }}</span>
        <span class="ui-badge ok">启用 {{ keys.filter((k) => k.enabled).length }}</span>
      </div>
      <section class="ui-panel" v-loading="loading">
        <div v-if="!loading && !keys.length" class="ui-empty">
          <div class="big">还没有 API Key</div>
          <p>创建一个 Key，第三方智能体就能通过 /api/* 调用平台能力。</p>
        </div>
        <table v-else class="ui-table">
          <thead>
            <tr>
              <th>名称</th><th style="width: 130px">前缀</th><th>权限</th>
              <th style="width: 100px; text-align: right">限流/分</th>
              <th style="width: 90px">状态</th>
              <th style="width: 90px; text-align: right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in keys" :key="row.id">
              <td class="strong">{{ row.name }}</td>
              <td class="num">{{ row.prefix }}</td>
              <td>
                <span v-for="s in row.scopes" :key="s" class="ui-badge acc" style="margin-right: 4px">{{ s }}</span>
              </td>
              <td class="num">{{ row.rate_limit_per_min }}</td>
              <td>
                <span class="ui-badge" :class="{ ok: row.enabled }">{{ row.enabled ? '启用' : '已吊销' }}</span>
              </td>
              <td style="text-align: right">
                <button class="ui-btn ghost sm danger" :disabled="!row.enabled" @click="confirmRevoke(row)">吊销</button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- MCP 接入 -->
    <template v-else-if="tab === 'mcp'">
      <section class="ui-panel">
        <div class="ui-panel-head">
          <h2>MCP 配置</h2>
          <span class="hint">粘贴到 Claude Code / Codex 的 MCP 配置里，AI 即可调用平台能力</span>
          <div class="grow" />
          <button class="ui-btn sm" @click="copyMcp">复制配置</button>
        </div>
        <div class="ui-panel-body">
          <pre class="code">{{ mcpConfig }}</pre>
        </div>
      </section>
      <section class="ui-panel">
        <div class="ui-panel-head"><h2>可用工具</h2><span class="hint">工具会按 Key 的权限范围放行</span></div>
        <div class="ui-panel-body tool-list">
          <div v-for="t in tools" :key="t.name" class="tool">
            <code>{{ t.name }}</code>
            <span>{{ t.desc }}</span>
            <span class="ui-badge">{{ t.scope }}</span>
          </div>
        </div>
      </section>
    </template>

    <!-- 调用日志 -->
    <template v-else>
      <div class="bar">
        <button class="ui-btn" :disabled="loadingLogs" @click="loadLogs">刷新</button>
        <span class="grow" />
        <span class="ui-badge">共 {{ logs.length }} 条</span>
      </div>
      <section class="ui-panel" v-loading="loadingLogs">
        <div v-if="!loadingLogs && !logs.length" class="ui-empty">
          <div class="big">还没有调用记录</div>
          <p>外部智能体通过 API Key 调用之后，这里会留下审计日志。</p>
        </div>
        <table v-else class="ui-table">
          <thead>
            <tr>
              <th style="width: 180px">时间</th>
              <th style="width: 80px; text-align: right">Key</th>
              <th style="width: 170px">工具</th>
              <th>参数</th>
              <th style="width: 80px">状态</th>
              <th style="width: 100px; text-align: right">耗时(ms)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in logs" :key="i">
              <td class="num">{{ fmt(row.created_at) }}</td>
              <td class="num">{{ row.key_id }}</td>
              <td class="strong">{{ row.tool }}</td>
              <td class="ellip">{{ row.params_summary || '—' }}</td>
              <td><span class="ui-badge" :class="row.status === 'ok' ? 'ok' : 'bad'">{{ row.status }}</span></td>
              <td class="num">{{ row.duration_ms }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- 吊销确认 -->
    <div v-if="revokeTarget" class="ui-modal" @click.self="revokeTarget = null">
      <div class="ui-modal-box">
        <div class="ui-panel-head"><h2>吊销 API Key</h2></div>
        <div class="ui-modal-body">
          <p>确定吊销「{{ revokeTarget.name }}」？吊销后立即失效，使用它的调用会全部失败。</p>
        </div>
        <div class="ui-modal-foot">
          <button class="ui-btn" @click="revokeTarget = null">取消</button>
          <button class="ui-btn danger" @click="doRevoke">确认吊销</button>
        </div>
      </div>
    </div>

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

const revokeTarget = ref<any>(null)

// MCP 工具清单（与后端 skills / ai_access 暴露的能力对应）
const tools = [
  { name: 'search_assets', desc: '按关键词/平台/分类检索素材', scope: 'read' },
  { name: 'get_asset', desc: '取单条素材详情、转录与拆解报告', scope: 'read' },
  { name: 'list_followed_creators', desc: '列出关注列表与同步状态', scope: 'read' },
  { name: 'create_crawl_task', desc: '按关键词建采集任务', scope: 'operate' },
  { name: 'trigger_sync', desc: '触发某个订阅/博主的同步', scope: 'operate' },
  { name: 'get_dashboard', desc: '读总览统计与趋势', scope: 'read' },
]

function switchLogs() {
  tab.value = 'logs'
  loadLogs()
}

function confirmRevoke(row: any) {
  revokeTarget.value = row
}

async function doRevoke() {
  if (!revokeTarget.value) return
  await api.delete(`/api/ai/keys/${revokeTarget.value.id}`)
  revokeTarget.value = null
  ElMessage.success('已吊销')
  await load()
}

async function copyMcp() {
  try {
    await navigator.clipboard.writeText(mcpConfig.value)
    ElMessage.success('已复制 MCP 配置')
  } catch {
    ElMessage.warning('浏览器不允许自动复制，请手动选择')
  }
}

onMounted(load)
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.bar .grow { margin-left: auto; }
.ellip { max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }

/* MCP 配置代码块 */
.code {
  margin: 0;
  padding: var(--sp-4);
  background: var(--sidebar-bg);
  color: var(--sidebar-ink);
  border: 1px solid var(--ink);
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  line-height: 1.6;
  overflow: auto;
  max-height: 420px;
  white-space: pre;
}

/* 工具清单 */
.tool-list { display: flex; flex-direction: column; gap: 8px; }
.tool {
  display: grid;
  grid-template-columns: 190px 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  border: 1px solid var(--ink);
  background: var(--surface);
  box-shadow: 2px 2px 0 var(--ink);
  font-size: var(--fs-sm);
}
.tool code { font-family: var(--font-mono); color: var(--accent); font-weight: 700; }
.tool span { color: var(--muted); }
@media (max-width: 820px) {
  .tool { grid-template-columns: 1fr; gap: 4px; }
}

/* 确认弹层 */
.ui-modal {
  position: fixed; inset: 0; z-index: 200;
  background: color-mix(in oklab, var(--ink) 45%, transparent);
  display: grid; place-items: center; padding: var(--sp-5);
}
.ui-modal-box { background: var(--surface); border: 2px solid var(--ink); box-shadow: var(--shadow-2); width: min(500px, 100%); }
.ui-modal-body { padding: var(--sp-5); color: var(--ink-2); }
.ui-modal-body p { margin: 0; }
.ui-modal-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: var(--sp-4) var(--sp-5); border-top: 1px solid var(--line-soft);
}
</style>
