<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>模型接入</h1>
      <span class="sub">导入模型，并把合适的工作分配给合适的模型</span>
      <span class="grow" />
      <button class="ui-btn" @click="refreshCurrent">刷新</button>
    </div>

    <section class="xingsuan-strip">
      <span class="xingsuan-mark" aria-hidden="true"><el-icon><StarFilled /></el-icon></span>
      <div class="xingsuan-copy">
        <strong>星算AI 已为本项目做模型调用深度优化</strong>
        <span>针对视频理解、结构化输出和主备模型切换链路做了适配。</span>
      </div>
      <div class="xingsuan-actions">
        <span class="xingsuan-note">其他模型仍可自由接入</span>
        <a class="ui-btn primary" :href="XINGSUAN_HOME" target="_blank" rel="noopener noreferrer">
          了解星算AI <el-icon><TopRight /></el-icon>
        </a>
      </div>
    </section>

    <div class="ui-tabs">
      <button class="ui-tab" :class="{ on: tab === 'models' }" @click="tab = 'models'">已导入模型</button>
      <button class="ui-tab" :class="{ on: tab === 'assignments' }" @click="tab = 'assignments'">工作分配</button>
      <button class="ui-tab" :class="{ on: tab === 'calls' }" @click="switchCalls">调用审计</button>
    </div>

    <template v-if="tab === 'models'">
      <div class="ui-bar">
        <button class="ui-btn primary" @click="openAdd">添加模型</button>
        <button class="ui-btn" @click="openProviders">供应商管理</button>
        <span class="grow" />
        <span class="ui-badge">共 {{ models.length }} 个模型</span>
        <span class="ui-badge ok">已启用 {{ models.filter((m) => m.enabled).length }}</span>
      </div>

      <section class="ui-panel" v-loading="loading">
        <div v-if="!loading && !models.length" class="ui-empty">
          <div class="big">还没有导入模型</div>
          <p>先添加一个模型，系统会自动检测它能做什么。</p>
          <button class="ui-btn primary" @click="openAdd">添加模型</button>
        </div>
        <table v-else class="ui-table">
          <thead>
            <tr>
              <th>模型</th>
              <th style="width: 170px">供应商</th>
              <th style="width: 230px">能力</th>
              <th style="width: 200px">当前工作</th>
              <th style="width: 110px">状态</th>
              <th style="width: 220px; text-align: right">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in models" :key="row.id">
              <tr class="clickable" @click="toggleExpand(row.id)">
                <td>
                  <div class="strong">{{ row.display_name || row.model_key }}</div>
                  <div class="mono dim">{{ row.model_key }}</div>
                </td>
                <td>{{ row.provider_name }}</td>
                <td>
                  <template v-if="row.provider_enabled === false">
                    <span class="ui-badge warn">供应商停用，能力暂不可用</span>
                  </template>
                  <template v-else>
                    <span v-for="cap in capabilityTags(row)" :key="cap.key" class="ui-badge" :class="cap.className" style="margin: 2px 4px 2px 0">
                      {{ cap.label }}
                    </span>
                  </template>
                </td>
                <td>{{ row.provider_enabled === false ? '供应商停用，不参与分配' : workLabels(row.assigned_work_types) }}</td>
                <td>
                  <span v-if="row.provider_enabled === false" class="ui-badge warn">供应商已停用</span>
                  <span v-else class="ui-badge" :class="probeClass(row.probe_status)">{{ probeLabel(row.probe_status) }}</span>
                </td>
                <td style="text-align: right">
                  <button class="ui-btn sm ghost" @click.stop="toggleExpand(row.id)">{{ expanded.has(row.id) ? '收起' : '详情' }}</button>
                  <button class="ui-btn sm" :disabled="row.probe_status === 'testing'" @click.stop="probe(row)">检测</button>
                  <button class="ui-btn sm" @click.stop="toggleEnabled(row)">{{ row.enabled ? '停用' : '启用' }}</button>
                  <button class="ui-btn sm danger" @click.stop="removeModel(row)">删除</button>
                </td>
              </tr>
              <tr v-if="expanded.has(row.id)" class="detail-row">
                <td colspan="6">
                  <div class="model-detail">
                    <div>
                      <b>协议</b>
                      <span>{{ row.protocol || '—' }}</span>
                      <span class="dim">（{{ protocolSourceLabel(row) }}）</span>
                    </div>
                    <div class="protocol-pick">
                      <label>协议设置</label>
                      <el-select
                        :model-value="row.protocol_override || 'inherit'"
                        size="small"
                        style="width: 200px"
                        @change="(v: string) => setModelProtocol(row, v)"
                      >
                        <el-option label="跟随供应商（默认）" value="inherit" />
                        <el-option label="OpenAI 兼容" value="openai" />
                        <el-option label="Anthropic 原生" value="anthropic" />
                        <el-option label="Gemini 原生" value="gemini" />
                      </el-select>
                      <span class="dim">选择后需重新检测</span>
                    </div>
                    <div><b>Base URL</b>{{ row.provider_base_url || '—' }}</div>
                    <div><b>检测结果</b>{{ row.probe_error || '暂无错误' }}</div>
                    <div><b>最近检测</b>{{ fmt(row.probed_at) }}</div>
                    <div><b>导入方式</b>通过供应商导入</div>
                    <div v-if="staleMessage(row)"><b>能力状态</b>{{ staleMessage(row) }}</div>
                    <div v-if="row.capabilities?.video?.source === 'manual_override'"><b>手工标记</b>未实测，运行失败时会照常记录</div>
                    <div v-else-if="canManuallyMarkVideo(row)"><b>视频能力</b><button class="ui-btn sm ghost" @click.stop="markVideoCapability(row)">手工标记为支持视频</button></div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </section>
    </template>

    <template v-else-if="tab === 'assignments'">
      <div class="assignment-grid" v-loading="loading">
        <section v-for="item in assignments" :key="item.work_type" class="ui-panel assignment-card">
          <div class="ui-panel-head">
            <div>
              <h2>{{ workTitle(item.work_type) }}</h2>
              <div class="hint">{{ workHint(item.work_type) }}</div>
            </div>
            <span class="grow" />
            <span class="ui-badge" :class="item.configured ? (item.auto_configured ? 'acc' : 'ok') : 'warn'">
              {{ item.configured ? (item.auto_configured ? '已自动配置' : '已手动配置') : '未配置' }}
            </span>
          </div>
          <div class="ui-panel-body assignment-body">
            <label>主模型</label>
            <el-select v-model="draft[item.work_type].primary_model_id" clearable placeholder="选择主模型" style="width: 100%">
              <el-option v-for="m in candidates(item.work_type)" :key="m.id" :label="m.display_name" :value="m.id" />
            </el-select>
            <label>备用模型（可选）</label>
            <el-select v-model="draft[item.work_type].fallback_model_id" clearable placeholder="不用备用模型" style="width: 100%">
              <el-option v-for="m in candidates(item.work_type)" :key="m.id" :label="m.display_name" :value="m.id" />
            </el-select>
            <p class="hint">主模型失败时自动尝试备用模型，失败原因会保留。</p>
            <button class="ui-btn primary" @click="saveAssignment(item.work_type)">保存</button>
          </div>
        </section>
      </div>
      <div class="ui-bar">
        <button class="ui-btn" @click="recommend">恢复自动推荐</button>
        <span class="hint">只会补空槽位，不覆盖你已经选好的模型。</span>
      </div>
    </template>

    <template v-else>
      <div class="ui-bar">
        <el-select v-model="callFilters.work_type" clearable placeholder="全部工作" style="width: 150px" @change="reloadCalls">
          <el-option v-for="(label, key) in workNames" :key="key" :label="label" :value="key" />
        </el-select>
        <el-select v-model="callFilters.status" clearable placeholder="全部状态" style="width: 140px" @change="reloadCalls">
          <el-option label="调用中" value="running" />
          <el-option label="成功" value="ok" />
          <el-option label="失败" value="error" />
        </el-select>
        <el-select v-model="callFilters.model_id" clearable filterable placeholder="全部模型" style="width: 210px" @change="reloadCalls">
          <el-option v-for="m in models" :key="m.id" :label="m.display_name || m.model_key" :value="m.id" />
        </el-select>
        <el-input v-model="callFilters.asset_id" clearable placeholder="素材 ID" style="width: 120px" @keyup.enter="reloadCalls" />
        <button class="ui-btn sm" @click="reloadCalls">查询</button>
        <span class="grow" />
        <span class="ui-badge">共 {{ callTotal }} 条调用</span>
      </div>

      <section class="ui-panel" v-loading="callsLoading">
        <div v-if="!callsLoading && !calls.length" class="ui-empty">
          <div class="big">还没有模型调用记录</div>
          <p>模型完成一次真实调用后，这里会记录模型、状态、耗时和原始报错。</p>
        </div>
        <table v-else class="ui-table">
          <thead>
            <tr>
              <th>时间</th>
              <th style="width: 130px">工作</th>
              <th style="width: 240px">模型</th>
              <th style="width: 150px">素材</th>
              <th style="width: 110px">角色</th>
              <th style="width: 100px">状态</th>
              <th style="width: 90px">耗时</th>
              <th style="width: 105px; text-align: right">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in calls" :key="row.id">
              <tr class="clickable" @click="toggleCall(row.id)">
                <td class="mono">{{ fmt(row.created_at) }}</td>
                <td>{{ callWorkLabel(row) }}</td>
                <td>
                  <div class="strong">{{ row.model_key || '未记录模型' }}</div>
                  <div class="dim">{{ row.provider_name || '供应商已删除' }}</div>
                </td>
                <td>
                  <template v-if="row.asset_id">
                    <div class="strong">{{ row.asset_title || `素材 #${row.asset_id}` }}</div>
                    <div class="mono dim">ID {{ row.asset_id }}</div>
                  </template>
                  <span v-else class="dim">未关联素材</span>
                </td>
                <td><span class="ui-badge" :class="row.role === 'fallback' ? 'acc' : ''">{{ row.role === 'fallback' ? '备用模型' : '主模型' }}</span></td>
                <td><span class="ui-badge" :class="callClass(row.status)">{{ callStatusLabel(row.status) }}</span></td>
                <td class="mono">{{ fmtDuration(row.duration_ms) }}</td>
                <td style="text-align: right"><button class="ui-btn sm ghost" @click.stop="toggleCall(row.id)">{{ expandedCalls.has(row.id) ? '收起' : '详情' }}</button></td>
              </tr>
              <tr v-if="expandedCalls.has(row.id)" class="detail-row">
                <td colspan="8">
                  <div class="call-detail">
                    <div><b>调用分组</b><span class="mono">{{ row.attempt_group_id }}</span></div>
                    <div><b>操作</b>{{ row.operation || '—' }}</div>
                    <div><b>HTTP 状态</b>{{ row.http_status ?? '—' }}</div>
                    <div><b>任务 ID</b>{{ row.job_id ?? '—' }}</div>
                    <div><b>错误分类</b>{{ row.error_class || '无' }}</div>
                    <div><b>结束时间</b>{{ row.finished_at ? fmt(row.finished_at) : '尚未完成' }}</div>
                    <div><b>Usage</b><span class="mono">{{ usageText(row.usage) }}</span></div>
                    <div class="call-group" v-if="groupCalls(row)?.length">
                      <b>同一次业务调用</b>
                      <div class="call-group-row" v-for="item in groupCalls(row)" :key="item.id">
                        <span class="ui-badge" :class="item.role === 'fallback' ? 'acc' : ''">{{ item.role === 'fallback' ? '备用' : '主' }}</span>
                        <span class="mono">{{ item.model_key }}</span>
                        <span class="ui-badge" :class="callClass(item.status)">{{ callStatusLabel(item.status) }}</span>
                        <span class="hint">{{ item.error_class || '无错误分类' }}</span>
                      </div>
                    </div>
                    <div class="call-error" v-if="row.error_message"><b>原始报错</b><pre>{{ row.error_message }}</pre></div>
                    <div class="call-error" v-else><b>原始报错</b><span>无</span></div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </section>

      <div class="ui-bar call-pager">
        <button class="ui-btn sm" :disabled="callOffset <= 0 || callsLoading" @click="changeCallPage(-1)">上一页</button>
        <span class="hint">第 {{ callOffset / callLimit + 1 }} 页，每页 {{ callLimit }} 条</span>
        <button class="ui-btn sm" :disabled="callOffset + callLimit >= callTotal || callsLoading" @click="changeCallPage(1)">下一页</button>
      </div>
    </template>

    <el-dialog v-model="addDialog" title="添加模型" width="720px" :close-on-click-modal="false">
      <div class="step-body">
        <label>供应商</label>
        <el-select v-model="providerPreset" style="width: 100%" @change="applyPreset">
          <el-option v-for="p in presets" :key="p.key" :label="p.label" :value="p.key" />
        </el-select>
        <a v-if="providerPreset === 'xingsuan'" class="xingsuan-link" :href="XINGSUAN_HOME" target="_blank" rel="noopener noreferrer">
          还没有星算AI密钥？前往官网获取 <el-icon><TopRight /></el-icon>
        </a>

        <div class="dialog-grid">
          <div>
            <label>请求地址</label>
            <el-input v-model="provider.base_url" placeholder="https://api.example.com/v1" />
          </div>
          <div>
            <label>请求协议</label>
            <el-select v-model="provider.protocol" style="width: 100%">
              <el-option label="OpenAI 兼容" value="openai" />
              <el-option label="Anthropic 原生" value="anthropic" />
              <el-option label="Gemini 原生" value="gemini" />
            </el-select>
          </div>
        </div>

        <label>API Key</label>
        <el-input v-model="provider.api_key" type="password" show-password placeholder="只保存加密后的密文" />

        <div class="ui-bar" style="margin-top: 4px">
          <button class="ui-btn sm" :disabled="discovering" @click="discover">
            {{ availableModels.length ? '重新拉取模型' : '拉取模型' }}
          </button>
          <span v-if="discoverMessage" class="hint">{{ discoverMessage }}</span>
        </div>
      </div>

      <div class="model-picker">
        <div class="model-picker-head">
          <el-input v-model="modelSearch" clearable placeholder="搜索模型名" style="width: 220px" />
          <span class="ui-badge">已勾选 {{ selectedModels.length }} / {{ availableModels.length }}</span>
          <span class="grow" />
          <button class="ui-btn sm ghost" :disabled="!availableModels.length" @click="selectAllModels">全选</button>
          <button class="ui-btn sm ghost" @click="selectedModels = []">清空</button>
        </div>
        <div v-if="!availableModels.length" class="model-picker-empty">
          填写请求地址与 API Key 后点「拉取模型」；也可以直接在下面手动输入模型名。
        </div>
        <el-checkbox-group v-else v-model="selectedModels" class="model-picker-list">
          <label v-for="name in filteredModels" :key="name" class="model-picker-row">
            <el-checkbox :value="name" />
            <span class="mono model-name">{{ name }}</span>
          </label>
        </el-checkbox-group>
        <div class="ui-bar" style="margin-top: 10px">
          <el-input v-model="manualModel" placeholder="清单里没有？手动输入模型名后回车" @keyup.enter="addManualModel" />
          <button class="ui-btn sm" @click="addManualModel">添加</button>
        </div>
      </div>

      <el-checkbox v-model="probeVideo" style="margin-top: 12px">
        检测视频能力（会产生极少量调用费用，将检测 {{ selectedModels.length }} 个模型）
      </el-checkbox>
      <p class="hint">
        导入为「覆盖」语义：以本次勾选为准，该供应商名下未勾选的模型会被删除；
        仍被工作分配引用的会先停用，不会静默清掉你的分配。
      </p>

      <template #footer>
        <button class="ui-btn" @click="addDialog = false">关闭</button>
        <button class="ui-btn primary" :disabled="!selectedModels.length || importing" @click="submitImport">
          {{ importing ? '导入中…' : '导入' }}
        </button>
      </template>
    </el-dialog>

    <el-dialog v-model="providerDialog" title="供应商管理" width="720px" :close-on-click-modal="false">
      <div v-if="editingProvider" class="step-body">
        <label>名称</label>
        <el-input v-model="providerForm.name" />
        <label>Base URL</label>
        <el-input v-model="providerForm.base_url" />
        <label>默认协议（供应商默认，模型未单独设置时生效）</label>
        <el-select v-model="providerForm.protocol" style="width: 100%">
          <el-option label="OpenAI 兼容" value="openai" />
          <el-option label="Anthropic 原生" value="anthropic" />
          <el-option label="Gemini 原生" value="gemini" />
        </el-select>
        <label>访问密钥（留空则不修改）</label>
        <el-input v-model="providerForm.api_key" type="password" show-password placeholder="只保存加密后的密文" />
        <p class="hint">修改 Base URL 或协议后，该供应商下的模型会标记为需重新检测。</p>
        <div class="ui-bar">
          <button class="ui-btn" @click="editingProvider = null">返回</button>
          <button class="ui-btn primary" @click="saveProvider">保存</button>
        </div>
      </div>
      <div v-else v-loading="providerLoading" class="provider-list">
        <div v-if="!providerLoading && !providers.length" class="ui-empty">还没有供应商，添加模型时会自动创建。</div>
        <div v-for="p in providers" :key="p.id" class="provider-row">
          <div class="provider-main">
            <div><b>{{ p.name }}</b><span class="ui-badge" :class="p.enabled ? 'ok' : 'warn'">{{ p.enabled ? '已启用' : '已停用' }}</span></div>
            <div class="mono dim">{{ p.base_url }}</div>
            <div class="hint">密钥 {{ p.has_api_key ? (p.api_key_masked || '已配置') : '未配置' }}</div>
            <div class="provider-metrics">
              <button
                class="metric"
                :title="p.balance_error ? `刷新失败：${p.balance_error}` : '点击刷新余额（手动）'"
                @click="refreshBalance(p)"
              >
                <span class="metric-label">余额</span>
                <span class="metric-value" :class="{ err: p.balance_error }">{{ fmtBalance(p) }}</span>
              </button>
              <button
                class="metric"
                :title="p.latency_error ? `测速失败：${p.latency_error}` : '点击测延迟（手动）'"
                @click="refreshLatency(p)"
              >
                <span class="metric-label">延迟</span>
                <span class="metric-value" :class="{ err: p.latency_error }">{{ fmtLatency(p) }}</span>
              </button>
            </div>
          </div>
          <div class="provider-actions">
            <button class="ui-btn sm" @click="editProvider(p)">编辑</button>
            <button class="ui-btn sm" @click="toggleProvider(p)">{{ p.enabled ? '停用' : '启用' }}</button>
            <button class="ui-btn sm danger" @click="removeProvider(p)">删除</button>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { StarFilled, TopRight } from '@element-plus/icons-vue'
import { api } from '../api'

const XINGSUAN_HOME = 'https://xsai5.xyz'

const tab = ref<'models' | 'assignments' | 'calls'>('models')
const loading = ref(false)
const models = ref<any[]>([])
const assignments = ref<any[]>([])
const expanded = reactive(new Set<number>())

const calls = ref<any[]>([])
const callsLoading = ref(false)
const callTotal = ref(0)
const callLimit = 50
const callOffset = ref(0)
const expandedCalls = reactive(new Set<number>())
const callGroups = reactive<Record<string, any[]>>({})
const callGroupLoading = reactive(new Set<string>())
const callFilters = reactive<{ work_type: string; status: string; model_id: number | null; asset_id: string }>({
  work_type: '',
  status: '',
  model_id: null,
  asset_id: '',
})

const addDialog = ref(false)
const providerPreset = ref('xingsuan')
const provider = reactive({ name: '', protocol: 'openai', base_url: '', api_key: '' })
const selectedModels = ref<string[]>([])
const availableModels = ref<string[]>([])
const modelSearch = ref('')
const manualModel = ref('')
const probeVideo = ref(true)
const discovering = ref(false)
const importing = ref(false)
const discoverMessage = ref('')
const providerDialog = ref(false)
const providerLoading = ref(false)
const providers = ref<any[]>([])
const editingProvider = ref<any | null>(null)
const providerForm = reactive({ name: '', protocol: 'openai', base_url: '', api_key: '' })

const presets = [
  { key: 'xingsuan', label: '星算AI（推荐）', protocol: 'openai', base_url: 'https://api.xsai5.xyz/v1' },
  { key: 'openai', label: 'OpenAI', protocol: 'openai', base_url: 'https://api.openai.com/v1' },
  { key: 'deepseek', label: 'DeepSeek', protocol: 'openai', base_url: 'https://api.deepseek.com/v1' },
  { key: 'kimi', label: 'Kimi（Moonshot）', protocol: 'openai', base_url: 'https://api.moonshot.cn/v1' },
  { key: 'qwen', label: '通义千问（兼容模式）', protocol: 'openai', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { key: 'glm', label: '智谱 GLM', protocol: 'openai', base_url: 'https://open.bigmodel.cn/api/paas/v4' },
  { key: 'openrouter', label: 'OpenRouter', protocol: 'openai', base_url: 'https://openrouter.ai/api/v1' },
  { key: 'anthropic', label: 'Anthropic', protocol: 'anthropic', base_url: 'https://api.anthropic.com' },
  { key: 'gemini', label: 'Gemini', protocol: 'gemini', base_url: 'https://generativelanguage.googleapis.com' },
  { key: 'custom', label: '自定义', protocol: 'openai', base_url: '' },
]

const draft = reactive<Record<string, { primary_model_id: number | null; fallback_model_id: number | null }>>({
  text_analysis: { primary_model_id: null, fallback_model_id: null },
  cover_classify: { primary_model_id: null, fallback_model_id: null },
  video_understanding: { primary_model_id: null, fallback_model_id: null },
})

const workNames: Record<string, string> = {
  text_analysis: '文字分析',
  cover_classify: '封面判类',
  video_understanding: '视频理解',
}

function workTitle(type: string) {
  return workNames[type] || type
}

function workHint(type: string) {
  if (type === 'text_analysis') return '读文字。分析速览（摘要 + 标签）与爆款拆解都走这里，深度拆解可选更强模型'
  if (type === 'cover_classify') return '读文字，也能看封面图，输出分类结果'
  return '真正看视频画面，结合转录输出结构化理解'
}

function workLabels(types: string[]) {
  return (types || []).map(workTitle).join('、') || '—'
}

function capabilityTags(row: any) {
  const caps = row.capabilities || {}
  const out = []
  const add = (key: string, label: string) => {
    const value = caps[key]
    if (!value) return
    const stale = value.source === 'stale'
    out.push({
      key,
      label: stale
        ? `${label}（需重新检测）`
        : value.source === 'manual_override'
          ? `${label}（手工）`
          : `${label}${value.value ? '' : '（不支持）'}`,
      className: stale ? 'warn' : value.value ? 'ok' : 'bad',
    })
  }
  add('text', '文字')
  add('image', '看图')
  add('video', '看视频')
  return out
}

function staleMessage(row: any) {
  const caps = row.capabilities || {}
  for (const key of ['text', 'image', 'video']) {
    if (caps[key]?.source === 'stale') return caps[key].detail || '供应商连接配置已变更，需重新检测'
  }
  return ''
}

function protocolSourceLabel(row: any) {
  const source = row.protocol_source || 'inherit'
  if (source === 'manual') return '手动指定'
  if (source === 'auto') return '自动检测'
  return `跟随供应商 ${row.provider_protocol || ''}`.trim()
}

async function setModelProtocol(row: any, value: string) {
  try {
    await api.patch(`/api/model-access/models/${row.id}`, value === 'inherit'
      ? { protocol_source: 'inherit' }
      : { protocol: value })
    await loadAll({ syncDrafts: false })
  } catch { /* axios 已提示 */ }
}

function canManuallyMarkVideo(row: any) {
  const video = row.capabilities?.video
  if (!row.capabilities || Object.keys(row.capabilities).length === 0) return false
  if (video?.source === 'stale') return false
  if (video?.value === true) return false
  return video?.source === 'probe' || video === undefined || video === null
}

function probeLabel(status: string) {
  return ({ untested: '未检测', testing: '检测中', passed: '已通过', partial: '部分通过', failed: '检测失败', skipped: '已跳过' } as any)[status] || status
}

function probeClass(status: string) {
  if (status === 'passed') return 'ok'
  if (status === 'failed') return 'bad'
  return 'warn'
}

function fmt(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—'
}

async function loadAll(options: { syncDrafts?: boolean } = {}) {
  loading.value = true
  try {
    const [m, a] = await Promise.all([
      api.get('/api/model-access/models'),
      api.get('/api/model-access/assignments'),
    ])
    models.value = m.data
    assignments.value = a.data.assignments
    if (options.syncDrafts !== false) {
      for (const item of assignments.value) {
        draft[item.work_type] = {
          primary_model_id: item.primary_model_id,
          fallback_model_id: item.fallback_model_id,
        }
      }
    }
  } finally {
    loading.value = false
  }
}

function candidates(workType: string) {
  return models.value.filter((m) => {
    if (!m.enabled || m.provider_enabled === false) return false
    const caps = m.capabilities || {}
    if (workType === 'video_understanding') return !!(caps.json?.value && caps.video?.value)
    return !!(caps.text?.value && caps.json?.value)
  })
}

function toggleExpand(id: number) {
  if (expanded.has(id)) expanded.delete(id)
  else expanded.add(id)
}

async function toggleCall(id: number) {
  if (expandedCalls.has(id)) expandedCalls.delete(id)
  else {
    expandedCalls.add(id)
    const row = calls.value.find((item) => item.id === id)
    if (row) await loadCallGroup(row.attempt_group_id)
  }
}

function switchCalls() {
  tab.value = 'calls'
  if (!callsLoading.value) reloadCalls()
}

function refreshCurrent() {
  if (tab.value === 'calls') return loadCalls()
  return loadAll()
}

function reloadCalls() {
  callOffset.value = 0
  return loadCalls()
}

function changeCallPage(delta: number) {
  callOffset.value = Math.max(0, callOffset.value + delta * callLimit)
  return loadCalls()
}

async function loadCalls() {
  callsLoading.value = true
  try {
    const params: Record<string, string | number> = { limit: callLimit, offset: callOffset.value }
    if (callFilters.work_type) params.work_type = callFilters.work_type
    if (callFilters.status) params.status = callFilters.status
    if (callFilters.model_id) params.model_id = callFilters.model_id
    const assetId = callFilters.asset_id.trim()
    if (assetId && !/^\d+$/.test(assetId)) {
      ElMessage.warning('素材 ID 请填数字')
      return
    }
    if (assetId) params.asset_id = Number(assetId)
    const { data, headers } = await api.get('/api/model-access/calls', { params })
    calls.value = data
    callTotal.value = Number(headers['x-total-count'] || data.length)
    for (const key of Object.keys(callGroups)) delete callGroups[key]
  } finally {
    callsLoading.value = false
  }
}

async function loadCallGroup(groupId: string) {
  if (!groupId || callGroups[groupId] || callGroupLoading.has(groupId)) return
  callGroupLoading.add(groupId)
  try {
    const { data } = await api.get(`/api/model-access/calls/${encodeURIComponent(groupId)}`)
    callGroups[groupId] = data
  } finally {
    callGroupLoading.delete(groupId)
  }
}

function groupCalls(row: any) {
  const rows = callGroups[row.attempt_group_id] || []
  return rows.length > 1 ? rows : []
}

function callWorkLabel(row: any) {
  return workTitle(row.work_type || '')
}

function callStatusLabel(status: string) {
  return ({ running: '调用中', ok: '成功', error: '失败' } as any)[status] || status
}

function callClass(status: string) {
  if (status === 'ok') return 'ok'
  if (status === 'error') return 'bad'
  return 'warn'
}

function fmtDuration(value: number) {
  if (!value) return '—'
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} s`
}

function usageText(usage: any) {
  if (!usage || !Object.keys(usage).length) return '无'
  return JSON.stringify(usage)
}

async function openProviders() {
  providerDialog.value = true
  editingProvider.value = null
  await loadProviders()
}

async function loadProviders() {
  providerLoading.value = true
  try {
    const { data } = await api.get('/api/model-access/providers')
    providers.value = data
  } finally {
    providerLoading.value = false
  }
}

function editProvider(row: any) {
  editingProvider.value = row
  Object.assign(providerForm, {
    name: row.name,
    protocol: row.protocol,
    base_url: row.base_url,
    api_key: '',
  })
}

async function saveProvider() {
  if (!editingProvider.value) return
  const payload: Record<string, string> = {
    name: providerForm.name,
    protocol: providerForm.protocol,
    base_url: providerForm.base_url,
  }
  if (providerForm.api_key) payload.api_key = providerForm.api_key
  await api.patch(`/api/model-access/providers/${editingProvider.value.id}`, payload)
  editingProvider.value = null
  await loadProviders()
  await loadAll({ syncDrafts: false })
}

async function toggleProvider(row: any) {
  await api.patch(`/api/model-access/providers/${row.id}`, { enabled: !row.enabled })
  await loadProviders()
  await loadAll({ syncDrafts: false })
}

async function removeProvider(row: any) {
  if (!confirm(`删除供应商「${row.name}」？该供应商下还有模型时无法删除，请先删除模型。`)) return
  try {
    await api.delete(`/api/model-access/providers/${row.id}`)
    await loadProviders()
    await loadAll({ syncDrafts: false })
  } catch { /* axios 已提示 */ }
}

function openAdd() {
  addDialog.value = true
  providerPreset.value = 'xingsuan'
  Object.assign(provider, { name: '星算AI', protocol: 'openai', base_url: 'https://api.xsai5.xyz/v1', api_key: '' })
  selectedModels.value = []
  availableModels.value = []
  modelSearch.value = ''
  manualModel.value = ''
  discoverMessage.value = ''
}

function applyPreset(key: string) {
  const preset = presets.find((p) => p.key === key)
  if (!preset) return
  provider.protocol = preset.protocol
  provider.base_url = preset.base_url
  provider.name = preset.key === 'xingsuan' ? '星算AI' : preset.label
}

const filteredModels = computed(() => {
  const keyword = modelSearch.value.trim().toLowerCase()
  if (!keyword) return availableModels.value
  return availableModels.value.filter((name) => name.toLowerCase().includes(keyword))
})

function selectAllModels() {
  selectedModels.value = [...filteredModels.value]
}

function addManualModel() {
  const name = manualModel.value.trim()
  if (!name) return
  if (!availableModels.value.includes(name)) availableModels.value = [...availableModels.value, name]
  if (!selectedModels.value.includes(name)) selectedModels.value = [...selectedModels.value, name]
  manualModel.value = ''
}

async function discover() {
  if (!provider.api_key.trim()) {
    discoverMessage.value = '请先填写 API Key'
    return
  }
  discovering.value = true
  discoverMessage.value = '正在读取模型清单…'
  try {
    const { data } = await api.post('/api/model-access/providers/preview-models', {
      protocol: provider.protocol,
      base_url: provider.base_url,
      api_key: provider.api_key,
      name: provider.name,
      preset_key: providerPreset.value,
    })
    availableModels.value = data.models || []
    selectedModels.value = selectedModels.value.filter((name) => availableModels.value.includes(name))
    discoverMessage.value = `找到 ${availableModels.value.length} 个模型`
  } catch {
    discoverMessage.value = '清单读取失败，可以在下面手动输入模型名'
  } finally {
    discovering.value = false
  }
}

async function submitImport() {
  importing.value = true
  try {
    const { data } = await api.post('/api/model-access/providers/import-models', {
      name: provider.name,
      preset_key: providerPreset.value,
      protocol: provider.protocol,
      base_url: provider.base_url,
      api_key: provider.api_key,
      models: selectedModels.value.map((model_key) => ({ model_key, display_name: model_key })),
      probe: { text: true, image: true, video: probeVideo.value },
    })
    const removed = data.removed?.length || 0
    const kept = data.kept_in_use?.length || 0
    let message = `已导入 ${selectedModels.value.length} 个模型`
    if (removed) message += `，下线删除 ${removed} 个`
    if (kept) message += `，${kept} 个仍在工作分配中已改为停用`
    ElMessage.success(message)
    addDialog.value = false
    await loadAll()
    startProbePolling()
  } catch { /* axios 已提示 */ } finally {
    importing.value = false
  }
}

async function refreshBalance(row: any) {
  try {
    await api.post(`/api/model-access/providers/${row.id}/refresh-balance`)
    await loadProviders()
  } catch { /* axios 已提示 */ }
}

async function refreshLatency(row: any) {
  try {
    await api.post(`/api/model-access/providers/${row.id}/refresh-latency`)
    await loadProviders()
  } catch { /* axios 已提示 */ }
}

function fmtBalance(row: any) {
  if (row.balance_amount === null || row.balance_amount === undefined) return '—'
  return `¥${Number(row.balance_amount).toFixed(2)}`
}

function fmtLatency(row: any) {
  if (row.latency_ms === null || row.latency_ms === undefined) return '—'
  return `${row.latency_ms}ms`
}

async function probe(row: any) {
  let wantsVideo = false
  try {
    await ElMessageBox.confirm(
      '同时检测视频能力会产生极少量调用费用；不含视频时只检测文字和图片。',
      '选择检测范围',
      {
        confirmButtonText: '包含视频',
        cancelButtonText: '不含视频',
        type: 'warning',
        distinguishCancelAndClose: true,
      },
    )
    wantsVideo = true
  } catch (action) {
    if (action === 'close') return
  }
  await api.post(`/api/model-access/models/${row.id}/probe`, { text: true, image: true, video: wantsVideo })
  await loadAll()
  startProbePolling()
}

async function markVideoCapability(row: any) {
  try {
    const { value } = await ElMessageBox.prompt(
      '只有在你已确认该模型能读取视频画面时再标记。请填写依据，运行失败仍会照常记录。',
      '手工标记视频能力',
      {
        confirmButtonText: '确认标记',
        cancelButtonText: '取消',
        inputPlaceholder: '例如：自有网关已实测支持视频',
        inputValidator: (text: string) => Boolean(text?.trim()) || '请填写标记依据',
      },
    )
    await api.post(`/api/model-access/models/${row.id}/manual-capability`, {
      video: true,
      reason: value.trim(),
    })
    await loadAll()
  } catch { /* 用户取消或接口已提示 */ }
}

let probePollTimer: number | undefined

function startProbePolling(attempt = 0) {
  window.clearTimeout(probePollTimer)
  if (attempt >= 140) return
  probePollTimer = window.setTimeout(async () => {
    try {
      const { data } = await api.get('/api/model-access/models')
      models.value = data
    } catch {
      return
    }
    if (models.value.some((m) => m.probe_status === 'testing')) startProbePolling(attempt + 1)
  }, 3000)
}

async function toggleEnabled(row: any) {
  await api.patch(`/api/model-access/models/${row.id}`, { enabled: !row.enabled })
  await loadAll({ syncDrafts: false })
}

async function removeModel(row: any) {
  if (!confirm(`删除模型「${row.display_name || row.model_key}」？`)) return
  try {
    await api.delete(`/api/model-access/models/${row.id}`)
    await loadAll({ syncDrafts: false })
  } catch { /* axios 已提示 */ }
}

async function saveAssignment(workType: string) {
  await api.put(`/api/model-access/assignments/${workType}`, draft[workType])
  await loadAll()
}

async function recommend() {
  await api.post('/api/model-access/assignments/recommend')
  await loadAll()
}

onMounted(async () => {
  await loadAll()
  if (models.value.some((m) => m.probe_status === 'testing')) startProbePolling()
})
onBeforeUnmount(() => window.clearTimeout(probePollTimer))
</script>

<style scoped>
.xingsuan-strip {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px 18px;
  padding: 14px 18px;
  border: 2px solid var(--ink);
  background: color-mix(in oklab, var(--accent-soft) 52%, var(--surface));
  box-shadow: var(--shadow-1);
}
.xingsuan-mark {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  background: var(--accent);
  color: var(--accent-ink);
  font-size: 18px;
  border: 1px solid var(--ink);
}
.xingsuan-copy { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.xingsuan-copy strong { color: var(--ink); font-size: var(--fs-base); }
.xingsuan-copy span { color: var(--ink-2); font-size: var(--fs-sm); }
.xingsuan-actions { display: flex; align-items: center; gap: 14px; }
.xingsuan-note { color: var(--muted); font-size: var(--fs-sm); white-space: nowrap; }
.xingsuan-actions .ui-btn { text-decoration: none; white-space: nowrap; }
.xingsuan-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  align-self: flex-start;
  color: var(--accent);
  font-size: var(--fs-sm);
  text-decoration: none;
}
.xingsuan-link:hover { text-decoration: underline; text-underline-offset: 3px; }
.mono { font-family: var(--font-mono); font-size: var(--fs-xs); }
.dim { color: var(--muted); margin-top: 2px; }
.detail-row td { background: var(--surface-2); }
.model-detail { display: flex; gap: 28px; flex-wrap: wrap; font-size: var(--fs-sm); color: var(--muted); }
.model-detail b { color: var(--ink-2); margin-right: 8px; }
.protocol-pick { display: flex; align-items: center; gap: 8px; }
.protocol-pick label { color: var(--ink-2); }
.call-detail { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 24px; font-size: var(--fs-sm); color: var(--muted); }
.call-detail > div { min-width: 0; }
.call-detail b { display: block; color: var(--ink-2); margin-bottom: 2px; }
.call-detail .mono { overflow-wrap: anywhere; }
.call-error { grid-column: 1 / -1; }
.call-group { grid-column: 1 / -1; }
.call-group-row {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 7px 0; border-top: 1px solid var(--line-soft);
}
.call-error pre {
  margin: 0; padding: 10px 12px; border: 1px solid var(--line-soft);
  background: var(--surface); color: var(--ink); font-family: var(--font-mono);
  font-size: var(--fs-sm); line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere;
  max-height: 260px; overflow: auto;
}
.call-pager { justify-content: center; }
.assignment-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--sp-5); }
.assignment-card .ui-panel-head { align-items: flex-start; }
.assignment-card .hint { margin-top: 3px; }
.assignment-body { display: flex; flex-direction: column; gap: 9px; }
.assignment-body label { margin-top: 6px; font-size: var(--fs-sm); color: var(--muted); }
.assignment-body .hint { margin: 2px 0 8px; line-height: 1.5; }
.step-body { display: flex; flex-direction: column; gap: 10px; }
.step-body label { font-size: var(--fs-sm); color: var(--muted); }
.provider-list { display: flex; flex-direction: column; gap: 10px; min-height: 80px; }
.provider-row {
  display: flex; align-items: center; gap: 16px; padding: 12px 14px;
  border: 1px solid var(--line); background: var(--surface-2);
}
.provider-main { min-width: 0; flex: 1; }
.provider-main > div:first-child { display: flex; align-items: center; gap: 8px; }
.provider-main .hint { margin-top: 3px; }
.provider-actions { display: flex; gap: 8px; flex: none; }
.provider-metrics { display: flex; gap: 18px; margin-top: 7px; }
.metric {
  display: flex; align-items: baseline; gap: 6px; padding: 0; border: 0;
  background: none; cursor: pointer; font: inherit; color: inherit;
}
.metric:hover .metric-value { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.metric-label { color: var(--muted); font-size: var(--fs-xs); }
.metric-value { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--ink-2); }
.metric-value.err { color: var(--danger, #c0392b); }
.dialog-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 12px; }
.dialog-grid label { display: block; margin-bottom: 4px; }
.model-picker { margin-top: 12px; border: 1px solid var(--line); background: var(--surface-2); }
.model-picker-head { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border-bottom: 1px solid var(--line-soft); }
.model-picker-empty { padding: 22px 12px; text-align: center; color: var(--muted); font-size: var(--fs-sm); }
.model-picker-list { display: block; max-height: 260px; overflow: auto; padding: 6px 0; }
.model-picker-row { display: flex; align-items: center; gap: 8px; padding: 3px 12px; cursor: pointer; }
.model-picker-row:hover { background: var(--surface); }
.model-name { overflow-wrap: anywhere; }
@media (max-width: 900px) {
  .xingsuan-strip { grid-template-columns: auto minmax(0, 1fr); }
  .xingsuan-actions { grid-column: 2; justify-content: space-between; }
}
@media (max-width: 620px) {
  .xingsuan-strip { grid-template-columns: 1fr; }
  .xingsuan-mark { display: none; }
  .xingsuan-actions { grid-column: 1; align-items: flex-start; flex-direction: column; gap: 8px; }
  .xingsuan-note { white-space: normal; }
}
@media (max-width: 1100px) { .assignment-grid { grid-template-columns: 1fr; } }
@media (max-width: 900px) { .call-detail { grid-template-columns: 1fr 1fr; } }
@media (max-width: 620px) { .call-detail { grid-template-columns: 1fr; } }
</style>
