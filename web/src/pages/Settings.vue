<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>系统设置</h1>
      <span class="sub">存储目录 · 加工分档 · 类型提示词 · 依赖诊断</span>
    </div>

    <!-- 页签头用 V3 样式；el-tabs 只保留内容切换能力（导航条由 CSS 隐藏） -->
    <div class="ui-tabs">
      <button v-for="t in tabs" :key="t.k" class="ui-tab" :class="{ on: tab === t.k }" @click="tab = t.k">
        {{ t.label }}
      </button>
    </div>
    <el-tabs v-model="tab" class="tabs-body">
      <!-- 存储目录 -->
      <el-tab-pane label="存储目录" name="storage">
        <p class="tip">素材保存根目录，支持本机路径、NAS 挂载，Windows 盘符或 UNC（如 Z:\xsai 或 \\NAS\xsai）。</p>
        <div class="bar">
          <button class="ui-btn primary" @click="storageDialog = true">新增根目录</button>
          <span class="grow" />
          <span class="ui-badge">共 {{ roots.length }}</span>
        </div>
        <section class="ui-panel" v-loading="loadingRoots">
          <div v-if="!loadingRoots && !roots.length" class="ui-empty">
            <div class="big">还没有配置存储根目录</div>
            <p>选一个目录作为素材落盘位置，下载与转录都会写到这里。</p>
          </div>
          <table v-else class="ui-table">
            <thead>
              <tr>
                <th style="width: 150px">名称</th>
                <th>路径</th>
                <th style="width: 80px">默认</th>
                <th style="width: 200px">可用性</th>
                <th style="width: 90px; text-align: right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in roots" :key="row.id">
                <td class="strong">{{ row.name }}</td>
                <td class="mono ellip" :title="row.path">{{ row.path }}</td>
                <td><span v-if="row.is_default" class="ui-badge acc">默认</span><span v-else class="muted">—</span></td>
                <td>
                  <span class="ui-badge" :class="row.available ? 'ok' : 'bad'">{{ row.available ? '可用' : '不可用' }}</span>
                  <span class="detail">{{ row.detail }}</span>
                </td>
                <td style="text-align: right">
                  <button class="ui-btn ghost sm danger" @click="delRoot(row)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      </el-tab-pane>

      <!-- 模型接入快捷入口：模型配置已独立成一级页面，避免两套入口并存 -->
      <el-tab-pane label="模型接入" name="llm">
        <section class="ui-panel">
          <div class="ui-panel-head">
            <div>
              <h2>模型接入</h2>
              <div class="tip" style="margin: 4px 0 0">导入供应商模型、检测能力，并给文字分析、封面判类、视频理解分别配置主备模型。</div>
            </div>
            <span class="grow" />
            <router-link class="ui-btn primary" to="/models">打开模型接入</router-link>
          </div>
          <div class="ui-panel-body">
            <p class="tip" style="margin: 0">系统会自动推荐合适模型；只有你手动改过的工作分配才会被视为人工配置。</p>
          </div>
        </section>
      </el-tab-pane>

      <!-- 评论抓取 -->
      <el-tab-pane label="评论抓取" name="comments">
        <p class="tip">抖音评论富集门控与抓取参数。仅对“够热”的作品抓评论，走 worker 队列，可随时调整。</p>
        <el-form label-width="200px" style="max-width: 640px">
          <el-form-item label="热门分位 top_pct">
            <el-input-number v-model="commentSettings.top_pct" :min="0" :max="1" :step="0.05" :precision="2" />
            <span class="detail">批内按互动分取前该比例的作品（0~1）</span>
          </el-form-item>
          <el-form-item label="单批预算 budget">
            <el-input-number v-model="commentSettings.budget" :min="0" :precision="0" />
            <span class="detail">每批最多入队多少个作品</span>
          </el-form-item>
          <el-form-item label="最低评论数 min_comments">
            <el-input-number v-model="commentSettings.min_comments" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="最低点赞数 min_likes">
            <el-input-number v-model="commentSettings.min_likes" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="评论互动权重 weight">
            <el-input-number v-model="commentSettings.weight" :min="0" :step="1" :precision="1" />
            <span class="detail">互动分 = 点赞 + 评论数 × weight</span>
          </el-form-item>
          <el-form-item label="抓取页数 pages">
            <el-input-number v-model="commentSettings.pages" :min="1" :precision="0" />
          </el-form-item>
          <el-form-item label="每页条数 per_page">
            <el-input-number v-model="commentSettings.per_page" :min="1" :precision="0" />
          </el-form-item>
          <el-form-item label="取 Top-N top_n">
            <el-input-number v-model="commentSettings.top_n" :min="1" :precision="0" />
            <span class="detail">本地按点赞排序后保留的条数</span>
          </el-form-item>
          <el-form-item label="翻页间隔（秒）interval">
            <el-input-number v-model="commentSettings.interval" :min="0" :step="0.5" :precision="2" />
          </el-form-item>
          <el-form-item label="抓取二级评论">
            <el-switch v-model="commentSettings.with_sub_comments" />
            <span class="detail">开启后每平台会额外抓取一级评论下的回复（请求量更大，默认关闭）</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveCommentSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 视频理解 -->
      <el-tab-pane label="视频理解" name="process">
        <p class="tip">素材分档加工门控（普通=转录 / 高级=视频理解）与类型提示词。阈值存 system_setting，可在线调整，不写死。</p>
        <el-form label-width="210px" style="max-width: 680px">
          <el-form-item label="高级档热度阈值 min_heat">
            <el-input-number v-model="proc.min_heat" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="最低点赞下限 min_likes_floor">
            <el-input-number v-model="proc.min_likes_floor" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="单批预算 budget">
            <el-input-number v-model="proc.budget" :min="0" :precision="0" />
            <span class="detail">全局限额：高级档在途 job（含 queued/running）上限；单条下载路径也受约束</span>
          </el-form-item>
          <el-form-item label="热门分位 top_pct">
            <el-input-number v-model="proc.top_pct" :min="0" :max="1" :step="0.05" :precision="2" />
            <span class="detail">仅批量路径（采集/同步）生效：每批按热度取前该比例升档；单条下载路径不适用</span>
          </el-form-item>
          <el-form-item label="点赞权重 weight_like">
            <el-input-number v-model="proc.weight_like" :min="0" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="评论权重 weight_comment">
            <el-input-number v-model="proc.weight_comment" :min="0" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="播放权重 weight_play">
            <el-input-number v-model="proc.weight_play" :min="0" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="视频内联上限 video_max_mb">
            <el-input-number v-model="proc.video_max_mb" :min="0" :step="1" :precision="2" />
          </el-form-item>
          <el-form-item label="封面内联上限 cover_max_mb">
            <el-input-number v-model="proc.cover_max_mb" :min="0" :step="1" :precision="2" />
          </el-form-item>
          <el-form-item label="低置信度阈值">
            <el-input-number v-model="proc.low_confidence_threshold" :min="0" :max="1" :step="0.05" :precision="2" />
          </el-form-item>
          <el-form-item label="开启判类 classify_enabled">
            <el-switch v-model="proc.classify_enabled" />
          </el-form-item>
          <el-form-item label="判类默认用封面 use_cover">
            <el-switch v-model="proc.use_cover" />
          </el-form-item>
          <el-form-item label="自动转录新素材">
            <el-switch v-model="proc.auto_transcribe" />
            <span class="detail">全局默认：素材下载完成后自动转录；订阅里可单独覆盖</span>
          </el-form-item>
          <el-form-item label="自动生成速览">
            <el-switch v-model="proc.auto_analyze" />
            <span class="detail">全局默认：转录后自动生成摘要 + 标签（速览）；深度爆款拆解仍需手动触发。订阅里可单独覆盖</span>
          </el-form-item>
          <el-alert type="info" :closable="false" show-icon style="margin-bottom: 14px"
            title="视频理解使用的模型已在「模型接入 → 工作分配」中设置。" />
          <el-form-item label="类型目录 taxonomy（JSON）">
            <el-input v-model="proc.taxonomyText" type="textarea" :rows="6" spellcheck="false" />
            <span class="detail">枚举 → 中文，可增删类型</span>
          </el-form-item>
          <el-form-item label="类型提示词 type_prompts（JSON）">
            <el-input v-model="proc.typePromptsText" type="textarea" :rows="10" spellcheck="false" />
            <span class="detail">类型 → {focus, dimensions:[{key,name,ask}]}，保存前本地校验</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveProcessSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 爆款拆解 -->
      <el-tab-pane label="爆款拆解" name="breakdown">
        <p class="tip">爆款拆解报告使用的提示词，以及「对标账号」里判定爆款的热度倍数（播放 ≥ 该账号自身中位数 × 倍数）。</p>
        <el-form label-width="140px" style="max-width: 720px">
          <el-form-item label="拆解提示词">
            <el-input v-model="bd.prompt" type="textarea" :rows="14" />
          </el-form-item>
          <el-form-item label="爆款倍数 viral_multiple">
            <el-input-number v-model="bd.viral_multiple" :min="0.1" :step="0.5" :precision="1" />
            <span class="detail">必须为正数</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveBreakdownSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 诊断 -->
      <el-tab-pane label="依赖诊断" name="diag">
        <el-button @click="loadDiag">重新检测</el-button>
        <el-table :data="checks" v-loading="loadingDiag" stripe style="margin-top: 12px">
          <el-table-column label="检查项" width="160">
            <template #default="{ row }">{{ row.name }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.ok ? 'success' : 'danger'" size="small">{{ row.ok ? 'OK' : '异常' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="280">
            <template #default="{ row }">{{ row.detail }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 管理员 -->
      <!-- 版本与升级 -->
      <el-tab-pane label="版本与升级" name="version">
        <el-descriptions :column="2" border style="max-width: 720px">
          <el-descriptions-item label="应用版本">{{ ver.app_version || '—' }}</el-descriptions-item>
          <el-descriptions-item label="提交">{{ ver.git_commit || '—' }}</el-descriptions-item>
          <el-descriptions-item label="数据库版本">{{ ver.db_revision || '—' }}</el-descriptions-item>
          <el-descriptions-item label="内置迁移 head">{{ ver.db_head || '—' }}</el-descriptions-item>
          <el-descriptions-item label="数据库同步">
            <el-tag :type="ver.db_in_sync ? 'success' : 'warning'" size="small" effect="dark">
              {{ ver.db_in_sync ? '已同步' : '未同步' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="构建时间">{{ ver.build_time || '—' }}</el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="update.available && update.url"
          type="warning"
          :closable="false"
          style="margin-top: 12px; max-width: 720px"
          title="发现新版本"
          :description="`最新 ${update.latest}，当前 ${update.current}。查看发布说明：${update.url}`"
          show-icon
        />

        <div style="margin-top: 20px; max-width: 720px">
          <el-alert
            v-if="!upgradeStatus.enabled"
            type="info"
            :closable="false"
            show-icon
            title="网页一键升级未启用"
            description="管理员未配置升级边车。请按 docs/upgrade-compatibility.md 用命令行升级（先 ./scripts/backup.sh，再 ./scripts/upgrade.sh <版本>）。"
          />
          <template v-else>
            <el-alert
              type="warning"
              :closable="false"
              show-icon
              title="网页升级会自动备份，失败自动回滚"
              description="升级流程：备份 → 拉取镜像 → 重建服务 → 健康校验；任一步失败会自动回滚到当前版本。过程中服务会短暂不可用。"
            />
            <div style="margin-top: 12px">
              <el-button
                v-if="upgradeTarget"
                type="primary"
                :loading="upgrading"
                :disabled="upgradeRunning"
                @click="confirmUpgrade"
              >升级到 {{ upgradeTarget }}</el-button>
              <el-button v-else type="primary" :loading="checkingUpdate" @click="checkUpdate">检查更新</el-button>
              <el-tag
                v-if="update.latest"
                :type="update.available ? 'warning' : 'success'"
                size="small"
                style="margin-left: 12px"
              >{{ update.available ? `有新版本 ${update.latest}` : '已是最新版本' }}</el-tag>
              <span v-if="upgradeTarget" class="upgrade-hint">当前 {{ update.current || ver.app_version }}</span>
            </div>

            <!-- 升级进度 -->
            <div v-if="upgradeJob" style="margin-top: 16px">
              <el-tag
                :type="jobTagType(upgradeJob.status)"
                size="small"
                effect="dark"
              >{{ jobLabel(upgradeJob.status) }}</el-tag>
              <span style="margin-left: 8px; color: var(--el-text-color-secondary)">
                {{ upgradeJob.from_version || '当前' }} → {{ upgradeJob.version }}
              </span>
              <el-steps direction="vertical" :active="jobActive(upgradeJob)" style="margin-top: 12px">
                <el-step
                  v-for="s in upgradeJob.steps"
                  :key="s.step + s.at"
                  :title="stepLabel(s.step)"
                  :status="s.status === 'ok' ? 'success' : s.status === 'error' ? 'error' : 'process'"
                  :description="s.detail"
                />
              </el-steps>
              <el-alert
                v-if="upgradeJob.error"
                :type="upgradeJob.status === 'rolled_back' ? 'warning' : 'error'"
                :closable="false"
                show-icon
                :title="upgradeJob.error"
                style="margin-top: 12px"
              />
              <div v-if="upgradeJob.backup_path" style="margin-top: 8px; color: var(--el-text-color-secondary); font-size: 13px">
                备份：{{ upgradeJob.backup_path }}
              </div>
            </div>
          </template>
        </div>
      </el-tab-pane>

      <!-- 管理员 -->
      <el-tab-pane label="管理员" name="admin">
        <el-form label-width="120px" style="max-width: 480px">
          <el-form-item label="原密码">
            <el-input v-model="pwd.old_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="新密码">
            <el-input v-model="pwd.new_password" type="password" show-password placeholder="至少 6 位" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="changePwd">修改密码</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <!-- 删除存储根确认 -->
    <div v-if="rootTarget" class="ui-modal" @click.self="rootTarget = null">
      <div class="ui-modal-box">
        <div class="ui-panel-head"><h2>删除存储根目录</h2></div>
        <div class="ui-modal-body">
          <p>确定删除「{{ rootTarget.name }}」({{ rootTarget.path }})？已入库素材的路径会失效。</p>
        </div>
        <div class="ui-modal-foot">
          <button class="ui-btn" @click="rootTarget = null">取消</button>
          <button class="ui-btn danger" @click="doDelRoot">确认删除</button>
        </div>
      </div>
    </div>

    <!-- 新增存储根弹窗 -->
    <el-dialog v-model="storageDialog" title="新增存储根目录" width="480px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="rootForm.name" placeholder="例如：本机素材 / NAS" />
        </el-form-item>
        <el-form-item label="路径">
          <div class="path-row">
            <el-input v-model="rootForm.path" placeholder="/mnt/nas/xsai-content" />
            <button type="button" class="ui-btn sm" @click="openPicker">浏览…</button>
          </div>
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="rootForm.is_default" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="storageDialog = false">取消</el-button>
        <el-button type="primary" @click="createRoot">保存</el-button>
      </template>
    </el-dialog>

    <!-- 目录浏览器：读取 backend/worker 进程可见的目录树（只读白名单），点选回填路径 -->
    <el-dialog v-model="pickerDialog" title="选择目录" width="580px" destroy-on-close append-to-body>
      <div class="picker">
        <div class="picker-roots">
          <span class="picker-label">根目录</span>
          <button
            v-for="r in fsRoots"
            :key="r.path"
            type="button"
            class="ui-btn sm"
            :class="{ on: browseRoot === r.path }"
            :disabled="!r.exists"
            :title="r.path + (r.exists ? '' : '（不可用）')"
            @click="loadDir(r.path)"
          >
            {{ rootLabel(r) }}
          </button>
          <span v-if="!fsRoots.length" class="picker-label">无可用根目录</span>
        </div>
        <div class="picker-crumbs">
          <button type="button" class="ui-btn sm ghost" :disabled="!browseParent" @click="loadDir(browseParent!)">
            ↑ 上一级
          </button>
          <span class="crumbs mono">
            <template v-for="(c, i) in crumbs" :key="c.path">
              <span v-if="i && !crumbs[i - 1].path.endsWith(browseSep)" class="sep">{{ browseSep }}</span>
              <a @click="loadDir(c.path)">{{ c.label }}</a>
            </template>
          </span>
        </div>
        <div class="picker-list" v-loading="loadingDir">
          <div v-if="!loadingDir && !browseEntries.length" class="picker-empty">此目录下没有子目录</div>
          <button
            v-for="e in browseEntries"
            :key="e.path"
            type="button"
            class="picker-item"
            @click="loadDir(e.path)"
          >
            <span class="pick-name">{{ e.name }}</span>
            <span class="grow" />
            <span v-if="!e.writable" class="ui-badge warn">只读</span>
          </button>
        </div>
        <div class="picker-manual">
          <el-input v-model="browseManual" placeholder="也可直接输入路径，如 /mnt/nas/xsai-content" size="small" />
          <button type="button" class="ui-btn sm" @click="loadDir(browseManual)">前往</button>
        </div>
      </div>
      <template #footer>
        <span class="picker-picked mono" :title="browsePath">{{ browsePath || '未选择目录' }}</span>
        <span class="grow" />
        <el-button @click="pickerDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!browsePath" @click="chooseDir">选用此目录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from '../api'

const tab = ref('storage')
// 页签清单：与下面 el-tab-pane 的 name 一一对应
const tabs = [
  { k: 'storage', label: '存储目录' },
  { k: 'llm', label: '模型接入' },
  { k: 'comments', label: '评论抓取' },
  { k: 'process', label: '视频理解' },
  { k: 'breakdown', label: '爆款拆解' },
  { k: 'diag', label: '依赖诊断' },
  { k: 'version', label: '版本与升级' },
  { k: 'admin', label: '管理员' },
]
const roots = ref<any[]>([])
const checks = ref<any[]>([])
const loadingRoots = ref(false)
const loadingDiag = ref(false)
// ---- 版本与升级 ----
const ver = ref<any>({ app_version: '', git_commit: '', build_time: '', db_revision: '', db_head: '', db_in_sync: false })
const update = ref<any>({ current: '', latest: '', available: false, url: '' })
const upgradeStatus = ref<{ enabled: boolean; current: string }>({ enabled: false, current: '' })
const checkingUpdate = ref(false)
const upgrading = ref(false)
const upgradeJob = ref<any>(null)
// 升级目标 = 检查更新发现的最新版；用户从不手填版本号。
const upgradeTarget = computed(() => (update.value.available ? update.value.latest : ''))
// 一次升级正在跑（点击后到轮询结束前），期间禁止重复触发。
const upgradeRunning = computed(() => upgrading.value || upgradeJob.value?.status === 'running')
const storageDialog = ref(false)
const rootForm = reactive({ name: '', path: '', is_default: false })

// ---- 目录浏览器（只读白名单，读取 backend/worker 进程可见的目录树）----
const pickerDialog = ref(false)
const fsRoots = ref<any[]>([])
const browseRoot = ref('')
const browsePath = ref('')
const browseParent = ref<string | null>(null)
const browseSep = ref('/')
const browseEntries = ref<any[]>([])
const browseManual = ref('')
const loadingDir = ref(false)

// 面包屑：从根到当前路径逐级切分（Windows 保留盘符前缀）。
const crumbs = computed(() => {
  const p = browsePath.value || ''
  if (!p) return []
  const sep = browseSep.value || '/'
  const out: { label: string; path: string }[] = []
  if (/^[A-Za-z]:[\\/]/.test(p)) {
    const drive = p.slice(0, 2) + sep
    out.push({ label: p.slice(0, 2), path: drive })
    let acc = drive
    for (const seg of p.slice(3).split(/[\\/]+/).filter(Boolean)) {
      acc = acc.endsWith(sep) ? acc + seg : acc + sep + seg
      out.push({ label: seg, path: acc })
    }
    return out
  }
  out.push({ label: sep, path: sep })
  let acc = ''
  for (const seg of p.split(/[\\/]+/).filter(Boolean)) {
    acc = acc + sep + seg
    out.push({ label: seg, path: acc })
  }
  return out
})

function rootLabel(r: any) {
  return r.path === '/' ? '根 /' : r.path
}

async function loadFsRoots() {
  const { data } = await api.get('/api/system/fs/roots')
  fsRoots.value = data.roots || []
}

async function loadDir(path: string) {
  loadingDir.value = true
  try {
    const { data } = await api.get('/api/system/fs/list', { params: { path } })
    browsePath.value = data.path
    browseParent.value = data.parent || null
    browseSep.value = data.sep || '/'
    browseEntries.value = data.entries || []
    browseManual.value = data.path
    // 高亮当前所属根（用于根快捷入口的激活态）
    const sep = data.sep || '/'
    const hit = (data.roots || []).find(
      (r: any) => data.path === r.path || data.path.startsWith(r.path === '/' ? '/' : r.path + sep),
    )
    if (hit) browseRoot.value = hit.path
  } catch (e: any) {
    // 400（越界/不存在）由 api 拦截器统一提示；这里保持当前目录不变
  } finally {
    loadingDir.value = false
  }
}

async function openPicker() {
  pickerDialog.value = true
  browseManual.value = rootForm.path || ''
  await loadFsRoots()
  // 优先打开当前已填路径；不在白名单（例如非本机风格的路径）时回第一个可用根。
  if (rootForm.path) await loadDir(rootForm.path)
  if (!browsePath.value) {
    const first = fsRoots.value.find((r) => r.exists) || fsRoots.value[0]
    await loadDir(first ? first.path : '')
  }
}

function chooseDir() {
  if (!browsePath.value) return
  rootForm.path = browsePath.value
  if (!rootForm.name.trim()) {
    const seg = browsePath.value.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || browsePath.value
    rootForm.name = seg
  }
  pickerDialog.value = false
}

const settings = reactive<any>({ llm_base_url: '', llm_model: '', llm_api_key: '', proxy_url: '', llm_api_key_set: false })
const pwd = reactive({ old_password: '', new_password: '' })
const commentSettings = reactive({
  top_pct: 0.2,
  budget: 10,
  min_comments: 20,
  min_likes: 100,
  weight: 20,
  pages: 2,
  per_page: 20,
  top_n: 20,
  interval: 1,
  with_sub_comments: false,
})
const proc = reactive<any>({
  min_heat: 1000,
  min_likes_floor: 100,
  budget: 20,
  top_pct: 0.2,
  weight_like: 1,
  weight_comment: 2,
  weight_play: 0,
  video_max_mb: 15,
  cover_max_mb: 5,
  low_confidence_threshold: 0.6,
  classify_enabled: true,
  use_cover: false,
  auto_transcribe: false,
  auto_analyze: false,
  taxonomyText: '{}',
  typePromptsText: '{}',
})

const bd = reactive<any>({ prompt: '', viral_multiple: 3 })

async function loadBreakdownSettings() {
  const { data } = await api.get('/api/system/breakdown-settings')
  bd.prompt = data.prompt ?? ''
  bd.viral_multiple = data.viral_multiple ?? 3
}

async function saveBreakdownSettings() {
  if (!bd.prompt || !bd.prompt.trim()) return ElMessage.warning('提示词不能为空')
  if (!(bd.viral_multiple > 0)) return ElMessage.warning('爆款倍数必须为正数')
  await api.put('/api/system/breakdown-settings', {
    prompt: bd.prompt,
    viral_multiple: bd.viral_multiple,
  })
  ElMessage.success('已保存')
  await loadBreakdownSettings()
}

async function loadRoots() {
  loadingRoots.value = true
  try {
    roots.value = (await api.get('/api/system/storage-roots')).data
  } finally {
    loadingRoots.value = false
  }
}

async function createRoot() {
  if (!rootForm.name.trim() || !rootForm.path.trim()) return ElMessage.warning('名称和路径必填')
  await api.post('/api/system/storage-roots', rootForm)
  storageDialog.value = false
  rootForm.name = ''
  rootForm.path = ''
  rootForm.is_default = false
  await loadRoots()
}

const rootTarget = ref<any>(null)

function delRoot(row: any) {
  rootTarget.value = row
}

async function doDelRoot() {
  if (!rootTarget.value) return
  await api.delete(`/api/system/storage-roots/${rootTarget.value.id}`)
  rootTarget.value = null
  await loadRoots()
}

async function loadSettings() {
  const { data } = await api.get('/api/system/settings')
  Object.assign(settings, data, { llm_api_key: '' })
}

async function saveSettings() {
  await api.put('/api/system/settings', {
    llm_base_url: settings.llm_base_url,
    llm_model: settings.llm_model,
    llm_api_key: settings.llm_api_key,
    proxy_url: settings.proxy_url,
  })
  ElMessage.success('已保存')
  settings.llm_api_key = ''
  await loadSettings()
}

const testingLlm = ref(false)
async function testLlm() {
  testingLlm.value = true
  try {
    // 用当前表单值（含未保存的）探测，方便边调边试
    const { data } = await api.post('/api/system/settings/test-llm', {
      llm_base_url: settings.llm_base_url,
      llm_model: settings.llm_model,
      llm_api_key: settings.llm_api_key,
    })
    if (data.ok) {
      ElMessage.success(`连接成功（${data.elapsed_ms}ms）· 模型 ${data.model}`)
    } else {
      ElMessage.error(data.message || '连接失败')
    }
  } finally {
    testingLlm.value = false
  }
}

async function loadDiag() {
  loadingDiag.value = true
  try {
    checks.value = (await api.get('/api/system/diagnostics')).data.checks
  } finally {
    loadingDiag.value = false
  }
}

async function loadVersion() {
  try {
    ver.value = (await api.get('/api/system/version')).data
    upgradeStatus.value = (await api.get('/api/system/update/status')).data
  } catch (e) {
    /* 版本信息获取失败不阻断设置页其它功能 */
  }
}

async function checkUpdate() {
  checkingUpdate.value = true
  try {
    const { data } = await api.get('/api/system/update/check')
    if (data.ok === false) {
      ElMessage.warning(data.message || '检查更新失败')
      return
    }
    update.value = data
    if (data.available) ElMessage.success(`发现新版本 ${data.latest}`)
    else ElMessage.success('已是最新版本')
  } finally {
    checkingUpdate.value = false
  }
}

function jobLabel(status: string) {
  return (
    { running: '升级进行中', success: '升级成功', failed: '升级失败', rolled_back: '已自动回滚' }[status] ||
    status
  )
}
function jobTagType(status: string) {
  return ({ running: 'warning', success: 'success', failed: 'danger', rolled_back: 'warning' } as any)[status] || 'info'
}
function stepLabel(step: string) {
  return (
    { backup: '备份', pull: '拉取镜像', recreate: '重建服务', verify: '健康校验', pin: '记录版本', rollback: '回滚' } as any
  )[step] || step
}
function jobActive(job: any) {
  return (job?.steps || []).filter((s: any) => s.status === 'ok').length
}

let pollTimer: number | null = null
function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

// 轮询升级进度。主 API 升级中会短暂不可达，此时保持上次状态、继续轮询。
function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    try {
      const { data } = await api.get('/api/system/upgrade/status')
      if (data?.job) upgradeJob.value = data.job
      const status = upgradeJob.value?.status
      if (status && status !== 'running') {
        stopPolling()
        if (status === 'success') ElMessage.success('升级成功，服务已更新')
        else if (status === 'rolled_back') ElMessage.warning('升级未成功，已自动回滚到原版本')
        else ElMessage.error('升级失败，请查看进度详情')
        // 升级结束后刷新版本信息
        await loadVersion()
      }
    } catch {
      /* 服务重启中，忽略，等待下次轮询 */
    }
  }, 2500)
}

async function confirmUpgrade() {
  const target = upgradeTarget.value
  if (!target) return ElMessage.warning('没有可升级的新版本，请先点「检查更新」')
  try {
    await ElMessageBox.confirm(
      `将升级到 ${target}。系统会先自动备份，然后拉取镜像并重建服务，期间服务短暂不可用；若新版本启动失败会自动回滚。是否继续？`,
      '确认升级',
      { type: 'warning', confirmButtonText: '开始升级', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  upgrading.value = true
  try {
    await api.post('/api/system/upgrade', { version: target })
    ElMessage.info('已开始升级，下方会显示进度')
    startPolling()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '升级失败')
  } finally {
    upgrading.value = false
  }
}

async function changePwd() {
  if (pwd.new_password.length < 6) return ElMessage.warning('新密码至少 6 位')
  await api.post('/api/auth/change-password', pwd)
  ElMessage.success('密码已修改')
  pwd.old_password = ''
  pwd.new_password = ''
}

async function loadCommentSettings() {
  Object.assign(commentSettings, (await api.get('/api/system/comment-settings')).data)
}

async function saveCommentSettings() {
  await api.put('/api/system/comment-settings', commentSettings)
  ElMessage.success('已保存')
}

async function loadProcessSettings() {
  const { data } = await api.get('/api/system/process-settings')
  Object.assign(proc, data, {
    taxonomyText: JSON.stringify(data.taxonomy ?? {}, null, 2),
    typePromptsText: JSON.stringify(data.type_prompts ?? {}, null, 2),
  })
}

async function saveProcessSettings() {
  let taxonomy: any
  let type_prompts: any
  try {
    taxonomy = JSON.parse(proc.taxonomyText)
    type_prompts = JSON.parse(proc.typePromptsText)
  } catch (e: any) {
    return ElMessage.error('JSON 解析失败：' + e.message)
  }
  await api.put('/api/system/process-settings', {
    min_heat: proc.min_heat,
    min_likes_floor: proc.min_likes_floor,
    budget: proc.budget,
    top_pct: proc.top_pct,
    weight_like: proc.weight_like,
    weight_comment: proc.weight_comment,
    weight_play: proc.weight_play,
    video_max_mb: proc.video_max_mb,
    cover_max_mb: proc.cover_max_mb,
    low_confidence_threshold: proc.low_confidence_threshold,
    classify_enabled: proc.classify_enabled,
    use_cover: proc.use_cover,
    auto_transcribe: proc.auto_transcribe,
    auto_analyze: proc.auto_analyze,
    taxonomy,
    type_prompts,
  })
  ElMessage.success('已保存')
  await loadProcessSettings()
}

onMounted(() => {
  loadRoots()
  loadSettings()
  loadDiag()
  loadCommentSettings()
  loadProcessSettings()
  loadBreakdownSettings()
  loadVersion()
})

onUnmounted(stopPolling)
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; margin-bottom: var(--sp-3); }
.bar .grow { margin-left: auto; }
.path-row { display: flex; gap: 8px; }
.path-row :deep(.el-input) { flex: 1; }
.upgrade-hint { margin-left: 12px; color: var(--muted); font-size: var(--fs-sm); }
.mono { font-family: var(--font-mono); font-size: var(--fs-sm); }
.ellip { max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.muted { color: var(--muted); }

/* 目录浏览器 */
.picker { display: flex; flex-direction: column; gap: 10px; }
.picker-roots { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.picker-roots .ui-btn.on { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.picker-label { color: var(--muted); font-size: var(--fs-sm); }
.picker-crumbs { display: flex; align-items: center; gap: 8px; min-width: 0; }
.picker-crumbs .crumbs { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.picker-crumbs .crumbs a { cursor: pointer; color: var(--ink-2); }
.picker-crumbs .crumbs a:hover { color: var(--accent); text-decoration: underline; }
.picker-crumbs .sep { margin: 0 2px; }
.picker-list { border: 1px solid var(--line-soft); border-radius: var(--radius); max-height: 300px; min-height: 120px; overflow-y: auto; }
.picker-item {
  width: 100%; display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; background: transparent; border: 0; border-bottom: 1px solid var(--line-soft);
  font: inherit; color: var(--ink-2); text-align: left; cursor: pointer;
}
.picker-item:last-child { border-bottom: 0; }
.picker-item:hover { background: color-mix(in oklab, var(--accent) 6%, transparent); }
.picker-item .grow { margin-left: auto; }
.picker-item .pick-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker-empty { padding: 28px 12px; text-align: center; color: var(--muted); }
.picker-manual { display: flex; gap: 8px; }
.picker-manual :deep(.el-input) { flex: 1; }
.picker-picked { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }

/* 隐藏 el-tabs 自带的导航条，只保留内容切换能力 */
.tabs-body :deep(.el-tabs__header) { display: none; }

/* 删除确认弹层 */
.ui-modal {
  position: fixed; inset: 0; z-index: 200;
  background: color-mix(in oklab, var(--ink) 45%, transparent);
  display: grid; place-items: center; padding: var(--sp-5);
}
.ui-modal-box { background: var(--surface); border: 2px solid var(--ink); box-shadow: var(--shadow-2); width: min(520px, 100%); }
.ui-modal-body { padding: var(--sp-5); color: var(--ink-2); }
.ui-modal-body p { margin: 0; word-break: break-all; }
.ui-modal-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: var(--sp-4) var(--sp-5); border-top: 1px solid var(--line-soft);
}
.tip {
  color: var(--muted);
  font-size: var(--fs-sm);
  margin: 4px 0 12px;
  line-height: 1.6;
}
.detail {
  color: #909399;
  font-size: 12px;
  margin-left: 8px;
}
</style>
