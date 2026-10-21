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
          <h2>3 步接入你的 AI 助手</h2>
          <span class="hint">不用懂代码。建好 Key，把提示词交给 Agent，它会自己装好并验证</span>
        </div>
        <div class="ui-panel-body quick-steps">
          <div class="quick-step" :class="{ done: !!keys.length }">
            <span class="qs-num">1</span>
            <div><b>创建 API Key</b><small>{{ keys.length ? `已创建 ${keys.length} 个` : '回到「API Key」页创建' }}</small></div>
          </div>
          <div class="quick-step" :class="{ done: !!mcpKey.trim() }">
            <span class="qs-num">2</span>
            <div><b>填写平台信息</b><small>{{ mcpKey.trim() ? '信息已就绪，可以复制提示词' : '在下方向导填写 Key 与地址' }}</small></div>
          </div>
          <div class="quick-step">
            <span class="qs-num">3</span>
            <div><b>交给 Agent 执行</b><small>复制提示词，粘贴给 Codex / Claude Code</small></div>
          </div>
        </div>
      </section>

      <section class="ui-panel">
        <div class="ui-panel-head">
          <h2>接入向导</h2>
          <span class="hint">三项填好后，下面的提示词就是完整可执行指令</span>
        </div>
        <div class="ui-panel-body guide-form">
          <label class="gfield">
            <span>平台地址</span>
            <el-input v-model="mcpBase" placeholder="http://10.0.0.23:9100">
              <template #append>
                <button class="ui-btn sm" @click="mcpBase = backendOrigin()">用当前地址</button>
              </template>
            </el-input>
            <small>Agent 与平台不在同一台机器时，填局域网 / Tailscale 地址，不要填 localhost。</small>
          </label>
          <label class="gfield">
            <span>API Key</span>
            <el-input v-model="mcpKey" type="password" show-password placeholder="sk-...（创建后只显示一次）" />
            <small>在「API Key」页创建后复制到此处。平台只存哈希，丢了只能重建。</small>
          </label>
          <label class="gfield">
            <span>platform-api 目录</span>
            <el-input v-model="mcpProjectDir" placeholder="填写部署 platform-api 代码的绝对路径" />
            <small>Agent 会在这个目录执行 <code>uv sync --extra mcp</code> 并启动 MCP。</small>
          </label>
        </div>
      </section>

      <section class="ui-panel prompt-panel">
        <div class="ui-panel-head">
          <h2>一键接入提示词</h2>
          <span class="hint">复制整段，粘贴给 Codex / Claude Code / Cursor 里的 Agent</span>
          <div class="grow" />
          <button class="ui-btn primary sm" @click="copyAgentPrompt">复制给 Agent</button>
        </div>
        <div class="ui-panel-body">
          <pre class="code prompt-code">{{ agentPrompt }}</pre>
          <p class="hint" style="display:block;margin-top:10px">
            Agent 会完成：安装 MCP → 自检 → 写客户端配置 → 用 list_accounts 验证连通。
            提示词含完整 Key，只在本地 / 私人 Agent 会话里粘贴，不要发到公开群聊或网页。
          </p>
        </div>
      </section>

      <section class="ui-panel">
        <div class="ui-panel-head">
          <h2>或者：手动配置</h2>
          <span class="hint">Agent 不方便执行终端命令时，用下面的配置自己贴</span>
          <div class="grow" />
          <button class="ui-btn sm" @click="copyMcp">复制 Codex 配置</button>
        </div>
        <div class="ui-panel-body">
          <ol class="steps">
            <li>装依赖：<code>cd {{ mcpProjectDir }} &amp;&amp; uv sync --extra mcp</code></li>
            <li>自检：<code>PLATFORM_API_BASE={{ apiBase }} PLATFORM_API_KEY={{ mcpKeyPreview }} uv run python -m mcp_server.server --self-check</code></li>
            <li>把下面配置贴进客户端，重启后用 <code>/mcp</code> 确认能看到 <code>xsai-platform</code>。</li>
          </ol>
          <p class="hint" style="display:block;margin:10px 0 6px">Codex · 追加到 <code>~/.codex/config.toml</code></p>
          <pre class="code">{{ mcpConfig }}</pre>
          <p class="hint" style="display:block;margin:14px 0 6px">Claude Code · 命令行执行一条</p>
          <pre class="code">{{ claudeCmd }}</pre>
          <p class="hint" style="display:block;margin:14px 0 0">
            完整步骤见 <code>docs/mcp-integration.md</code>；排错先跑
            <code>uv run python -m mcp_server.server --self-check</code>。
          </p>
        </div>
      </section>
      <section class="ui-panel">
        <div class="ui-panel-head"><h2>可用工具（{{ tools.length }}）</h2><span class="hint">工具会按 Key 的权限范围放行</span></div>
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
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { parseUtc } from '../utils/time'

const tab = ref('keys')
const keys = ref<any[]>([])
const logs = ref<any[]>([])
const loading = ref(false)
const loadingLogs = ref(false)
const dialog = ref(false)
const showKey = ref(false)
const newKey = ref('')
const form = reactive({ name: '', scopes: ['read', 'operate'], rate_limit_per_min: 60 })

// MCP 接入向导：用户只填三项，其余自动生成。
/** MCP 必须连后端而不是 Vite 页面：开发态 5173 自动换成同主机的 9100。 */
function backendOrigin() {
  try {
    const url = new URL(location.origin)
    if (url.port === '5173') url.port = '9100'
    return url.origin
  } catch {
    return location.origin
  }
}

const mcpBase = ref(backendOrigin())
const mcpKey = ref('')
const mcpProjectDir = ref('')

function maskKey(value: string) {
  const key = (value || '').trim()
  if (!key) return 'sk-你的key'
  if (key.length <= 12) return key
  return `${key.slice(0, 8)}…${key.slice(-4)}`
}

const mcpKeyPreview = computed(() => maskKey(mcpKey.value))

// MCP 走本地 stdio 进程（不是 HTTP URL）：这里给出可直接粘贴的配置。
const apiBase = computed(() => mcpBase.value.trim() || backendOrigin())
const projectDir = computed(() => mcpProjectDir.value.trim())
const missingProjectDir = computed(() => !projectDir.value)
const mcpConfig = computed(
  () => missingProjectDir.value
    ? '请先填写 platform-api 代码目录，再生成 MCP 配置。'
    : `[mcp_servers.xsai-platform]
command = "uv"
args = ["run", "--project", "${projectDir.value}", "python", "-m", "mcp_server.server"]
cwd = "${projectDir.value}"

[mcp_servers.xsai-platform.env]
PLATFORM_API_BASE = "${apiBase.value}"
PLATFORM_API_KEY  = "${mcpKey.value.trim() || 'sk-你的key'}"`,
)
const claudeCmd = computed(
  () => missingProjectDir.value
    ? '请先填写 platform-api 代码目录，再生成 Claude Code 命令。'
    : `claude mcp add xsai-platform \\
  --env PLATFORM_API_BASE=${apiBase.value} \\
  --env PLATFORM_API_KEY=${mcpKey.value.trim() || 'sk-你的key'} \\
  -- uv run --project ${projectDir.value} python -m mcp_server.server`,
)

const agentPrompt = computed(() => [
  '你是我的自动化接入助手。请把「星采 Stargather」通过 MCP 接入当前 AI 客户端，全程自己执行命令，不要让我手动改文件。',
  '',
  '平台信息：',
  `- 平台 API 地址：${apiBase.value}`,
  `- 平台 API Key：${mcpKey.value.trim() || '（请先提醒我在「AI 接入」页创建并粘贴 API Key）'}`,
  `- platform-api 项目目录：${projectDir.value || '（请先在页面上填写）'}`,
  '',
  '你要执行的步骤：',
  missingProjectDir.value
    ? '1. 先向我要 platform-api 的部署目录，再执行该目录下的 uv sync --extra mcp。'
    : `1. 进入项目目录并安装 MCP 依赖：cd ${projectDir.value} && uv sync --extra mcp`,
  `2. 运行自检，确认后端连通、Key 有效、14 个工具可见：PLATFORM_API_BASE=${apiBase.value} PLATFORM_API_KEY=<上面的Key> uv run python -m mcp_server.server --self-check`,
  '3. 自检通过后，把 MCP 配置写入当前客户端：Codex 写 ~/.codex/config.toml；Claude Code 执行下面这条 claude mcp add 命令。',
  mcpConfig.value,
  claudeCmd.value,
  '4. 重启或刷新 MCP 连接，执行 /mcp，确认出现 xsai-platform。',
  '5. 用 list_accounts 工具试调一次，并把结果告诉我。',
  '',
  '规则：',
  '- 不要修改平台业务代码，只写 MCP 配置。',
  `- 不要在聊天里输出完整 Key，需要展示时用 ${mcpKeyPreview.value} 代替。`,
  '- 如果任何一步失败，先运行自检命令，把完整报错贴给我再停止。',
].join('\n'))

function fmt(v: string | null) {
  return parseUtc(v)?.toLocaleString('zh-CN', { hour12: false }) ?? '—'
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

// MCP 工具清单（与 mcp_server/server.py 注册的 14 个工具一致）
const tools = [
  { name: 'list_accounts', desc: '列出所有账号及登录态', scope: 'read' },
  { name: 'check_account', desc: '立即体检某账号', scope: 'operate' },
  { name: 'start_qr_login', desc: '发起扫码登录，返回二维码', scope: 'operate' },
  { name: 'poll_qr_login', desc: '轮询扫码状态，成功后账号入库', scope: 'operate' },
  { name: 'update_cookie', desc: '手动用 Cookie 录入/更新账号', scope: 'operate' },
  { name: 'list_followed_creators', desc: '列出关注博主与同步状态', scope: 'read' },
  { name: 'create_crawl_task', desc: '按关键词建采集任务', scope: 'operate' },
  { name: 'list_crawl_tasks', desc: '列出采集任务', scope: 'read' },
  { name: 'trigger_sync', desc: '触发订阅/博主同步', scope: 'operate' },
  { name: 'search_assets', desc: '搜索素材库', scope: 'read' },
  { name: 'get_transcript', desc: '取某素材转录文本', scope: 'read' },
  { name: 'get_analytics', desc: '数据看板摘要', scope: 'read' },
  { name: 'export_assets', desc: '导出素材转录+分析（md/json）', scope: 'read' },
  { name: 'import_article', desc: '导入单篇公众号文章', scope: 'operate' },
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
  if (!mcpProjectDir.value.trim()) {
    ElMessage.warning('请先填写 platform-api 代码目录')
    return
  }
  try {
    await navigator.clipboard.writeText(mcpConfig.value)
    ElMessage.success('已复制 MCP 配置')
  } catch {
    ElMessage.warning('浏览器不允许自动复制，请手动选择')
  }
}

async function copyAgentPrompt() {
  if (!mcpKey.value.trim()) {
    ElMessage.warning('请先填写 API Key（在「API Key」页创建后复制过来）')
    return
  }
  if (!mcpProjectDir.value.trim()) {
    ElMessage.warning('请先填写 platform-api 代码目录')
    return
  }
  try {
    await navigator.clipboard.writeText(agentPrompt.value)
    ElMessage.success('已复制，粘贴给 Codex / Claude Code 即可')
  } catch {
    ElMessage.warning('浏览器不允许自动复制，请手动选择整段提示词')
  }
}

onMounted(load)
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.bar .grow { margin-left: auto; }
.ellip { max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }

/* MCP 配置代码块 */
.steps { margin: 0 0 4px; padding-left: 20px; color: var(--ink-2); font-size: var(--fs-sm); line-height: 1.9; }
.steps code, .ui-panel-body > .hint code { font-family: var(--font-mono); }
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

/* 新手向导：三步概览 + 三项输入 + 可复制提示词 */
.quick-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--sp-3); }
.quick-step {
  display: flex; align-items: flex-start; gap: 10px;
  padding: var(--sp-4); border: 1px solid var(--line-soft); background: var(--surface-2);
}
.quick-step.done {
  border-color: var(--ok);
  background: color-mix(in oklab, var(--ok) 8%, var(--surface));
}
.qs-num {
  flex: none; width: 24px; height: 24px; display: grid; place-items: center;
  background: var(--ink); color: var(--bg); font-weight: 800; font-size: var(--fs-sm);
}
.quick-step.done .qs-num { background: var(--ok); }
.quick-step b { display: block; font-size: var(--fs-sm); }
.quick-step small { display: block; margin-top: 2px; color: var(--muted); font-size: var(--fs-xs); line-height: 1.5; }
.guide-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--sp-4); }
.gfield { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.gfield > span { font-size: var(--fs-sm); font-weight: 700; }
.gfield small { color: var(--muted); font-size: var(--fs-xs); line-height: 1.55; }
.gfield code { font-family: var(--font-mono); }
.prompt-code { max-height: 360px; border-color: var(--accent); }
@media (max-width: 820px) {
  .tool { grid-template-columns: 1fr; gap: 4px; }
  .quick-steps, .guide-form { grid-template-columns: 1fr; }
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
