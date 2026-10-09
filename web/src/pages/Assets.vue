<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>素材库</h1>
      <span class="sub">共 {{ assets.length }} 条 · 视频可播放，文章可阅读</span>
      <div class="grow" />
      <button class="ui-btn" @click="load">刷新</button>
      <button class="ui-btn" @click="importDialog = true">导入文章</button>
      <button class="ui-btn primary" :disabled="!selected.length" @click="exportSel('md')">
        导出 Markdown
      </button>
      <button class="ui-btn" :disabled="!selected.length" @click="exportSel('json')">导出 JSON</button>
    </div>

    <!-- 筛选条：类型 / 平台 / 分类 / 状态 -->
    <div class="ui-filters">
      <div class="ui-seg">
        <button v-for="v in typeTabs" :key="v.k" :class="{ on: filters.content_type === v.k }" @click="setType(v.k)">
          {{ v.label }}
        </button>
      </div>
      <el-select v-model="filters.platform" placeholder="平台" clearable size="small" style="width: 130px" @change="load">
        <el-option label="B站" value="bilibili" />
        <el-option label="抖音" value="douyin" />
        <el-option label="快手" value="kuaishou" />
        <el-option label="知乎" value="zhihu" />
        <el-option label="小红书" value="xhs" />
        <el-option label="微信公众号" value="wechat_mp" />
      </el-select>
      <el-select v-model="filters.account_id" placeholder="账号" clearable size="small" style="width: 170px" @change="load">
        <el-option
          v-for="a in filteredAccounts"
          :key="a.id"
          :label="a.display_name || `${a.platform} #${a.id}`"
          :value="a.id"
        />
      </el-select>
      <el-select v-model="filters.category" placeholder="分类" clearable size="small" style="width: 150px" @change="load">
        <el-option v-for="(label, key) in taxonomy" :key="key" :label="label" :value="key" />
      </el-select>
      <el-select v-model="filters.transcribe_status" placeholder="转录状态" clearable size="small" style="width: 130px" @change="load">
        <el-option label="待转录" value="pending" />
        <el-option label="已完成" value="done" />
        <el-option label="失败" value="error" />
      </el-select>
      <span class="grow" />
      <span v-if="selected.length" class="ui-badge acc">已选 {{ selected.length }} 条</span>
    </div>

    <!-- 卡片网格 -->
    <div v-loading="loading">
      <div v-if="!loading && !assets.length" class="ui-empty">
        <div class="big">还没有素材</div>
        <p>去「关键词采集」或「关注列表」跑一次同步，素材会自动入库。</p>
      </div>
      <div v-else class="asset-grid">
        <article v-for="a in assets" :key="a.asset_id" class="asset" :class="{ picked: isPicked(a) }">
          <!-- 封面：整块可点即开始（视频播放 / 文章阅读），不再需要额外按钮 -->
          <div
            class="cover"
            :class="{ clickable: canOpen(a) }"
            :role="canOpen(a) ? 'button' : undefined"
            :title="coverHint(a)"
            @click="onCoverClick(a)"
          >
            <!-- 封面用真实 <img>：加载失败时自动退回占位符，避免留下空白/破图 -->
            <img v-if="a.cover_url && !coverFailed(a.asset_id)" :src="a.cover_url" alt=""
              loading="lazy" @error="markCoverFailed(a.asset_id)" />
            <span v-else class="ph">{{ typeLabel(a.content_type) }}</span>
            <span class="badge-plat"><PlatformMark :platform="a.platform" :show-label="false" size="sm" :colored="false" /></span>
            <span class="badge-type">{{ typeLabel(a.content_type) }}</span>
            <span v-if="a.duration_sec" class="badge-dur">{{ mmss(a.duration_sec) }}</span>
            <!-- 悬浮提示：点封面即可开始 -->
            <span v-if="canOpen(a)" class="cover-cta">
              <span class="cta-ic">{{ a.content_type === 'video' ? '▶' : '📖' }}</span>
              {{ a.content_type === 'video' ? '播放' : '阅读' }}
            </span>
            <span v-else-if="a.content_type === 'video'" class="badge-nofile">未下载</span>
          </div>

          <div class="body">
            <div class="title" :title="a.title">{{ a.title || '（无标题）' }}</div>

            <!-- 博主：头像 + 昵称，可点跳主页 -->
            <div class="author">
              <span class="ava" :style="a.author_avatar ? { backgroundImage: `url(${a.author_avatar})` } : {}">
                {{ a.author_avatar ? '' : (a.author_name || '?').slice(0, 1) }}
              </span>
              <a v-if="a.author_home_url" :href="a.author_home_url" target="_blank" rel="noopener" class="aname">
                {{ a.author_name || a.author_id }}
              </a>
              <span v-else class="aname">{{ a.author_name || '未知博主' }}</span>
            </div>

            <!-- 分类 + 发布时间 + 同步时间 -->
            <div class="meta-rows">
              <div class="mrow">
                <span class="mk">分类</span>
                <span v-if="a.auto_category" class="ui-badge acc">
                  {{ taxonomy[a.auto_category] || a.auto_category }}
                  <em v-if="a.category_confidence">· {{ (a.category_confidence * 100).toFixed(0) }}%</em>
                </span>
                <span v-else class="ui-badge none">未判类</span>
              </div>
              <div class="mrow"><span class="mk">发布</span><span class="mv">{{ fmtDate(a.published_at) }}</span></div>
              <div class="mrow"><span class="mk">同步</span><span class="mv">{{ fmtTime(a.synced_at || a.created_at) }}</span></div>
            </div>

            <!-- 指标 -->
            <div class="nums">
              <span><b>{{ compact(a.metrics?.play) }}</b> 播放</span>
              <span><b>{{ compact(a.metrics?.like) }}</b> 赞</span>
              <span><b>{{ compact(a.metrics?.comment) }}</b> 评论</span>
              <span><b>{{ compact(a.metrics?.favorite) }}</b> 收藏</span>
            </div>

            <div class="foot">
              <span class="size">{{ bytes(a.size_bytes) }}</span>
              <span class="ui-badge" :class="statusTone(a.download_status)">{{ s(a.download_status) }}</span>
              <span class="grow" />
              <label v-if="selected.length || pickMode" class="pick">
                <input type="checkbox" :checked="isPicked(a)" @change="togglePick(a)" />
              </label>
            </div>

            <div class="acts">
              <button class="ui-btn sm" @click="openDetail(a.asset_id)">详情</button>
              <button class="ui-btn sm" @click="act(a.asset_id, 'transcribe')">
                {{ isTranscribed(a) ? '重新转录' : '转录' }}
              </button>
              <button class="ui-btn sm" @click="act(a.asset_id, 'analyze')">
                {{ isAnalyzed(a) ? '重新分析' : '分析' }}
              </button>
            </div>
          </div>
        </article>
      </div>
    </div>

    <!-- 视频播放器（走 Range 流式接口，可拖进度条） -->
    <div v-if="player" class="ui-modal" @click.self="player = null">
      <div class="ui-modal-box player-box">
        <div class="ui-panel-head">
          <h2>{{ player.title || '（无标题）' }}</h2>
          <div class="grow" />
          <button class="ui-btn ghost sm" @click="player = null">关闭</button>
        </div>
        <div class="player-wrap">
          <video
            :src="`/api/assets/${player.asset_id}/media`"
            controls
            autoplay
            playsinline
            preload="metadata"
          />
        </div>
        <div class="player-meta">
          <PlatformMark :platform="player.platform" size="sm" />
          <span>· {{ player.author_name || '未知博主' }}</span>
          <span>{{ fmtDate(player.published_at) }} 发布 · {{ bytes(player.size_bytes) }}</span>
        </div>
      </div>
    </div>

    <!-- 文章阅读视图 -->
    <div v-if="article" class="ui-modal article-modal" @click.self="article = null">
      <div class="ui-modal-box">
        <div class="ui-panel-head">
          <h2>{{ article.title }}</h2>
          <div class="grow" />
          <a v-if="article.url" class="ui-btn sm" :href="article.url" target="_blank" rel="noopener">打开原文</a>
          <button class="ui-btn ghost sm" @click="article = null">关闭</button>
        </div>
        <div class="ui-modal-body article-body">
          <p class="art-meta">{{ article.author_name }} · {{ fmtDate(article.published_at) }}</p>
          <div v-if="article.html" class="art-html" v-html="article.html" />
          <pre v-else class="art-text">{{ article.text }}</pre>
        </div>
      </div>
    </div>

    <el-dialog v-model="importDialog" title="导入微信公众号文章" width="520px">
      <el-input v-model="importUrl" placeholder="https://mp.weixin.qq.com/s/..." clearable />
      <p class="hint">仅支持单篇文章链接。批量历史文章列表接口微信已关闭，无法导入整号历史。</p>
      <template #footer>
        <el-button @click="importDialog = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="doImport">导入</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="drawer" title="素材详情" size="50%" @close="closeDrawer">
      <div v-if="detail">
        <h3>{{ detail.content?.title }}</h3>
        <p class="meta"><PlatformMark :platform="detail.content?.platform" size="sm" /> · {{ detail.content?.author_name }}</p>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="文件目录">{{ detail.file?.full_dir || '—' }}</el-descriptions-item>
          <el-descriptions-item label="视频文件">{{ detail.file?.video_file || '—' }}</el-descriptions-item>
          <el-descriptions-item label="链接"><a :href="detail.content?.url" target="_blank">打开原页面</a></el-descriptions-item>
        </el-descriptions>

        <el-divider>转录文本</el-divider>
        <el-input v-if="detail.transcript" type="textarea" :rows="10" :model-value="detail.transcript.full_text" readonly />
        <el-empty v-else description="尚无转录" :image-size="60" />

        <el-divider>AI 分析</el-divider>
        <div v-if="detail.analysis">
          <p><b>摘要：</b>{{ detail.analysis.summary }}</p>
          <el-tag v-for="tag in detail.analysis.tags" :key="tag" style="margin-right: 6px">{{ tag }}</el-tag>
        </div>
        <el-empty v-else description="尚无分析" :image-size="60" />

        <el-divider>评论（按点赞排序）</el-divider>
        <p class="comment-total">评论总数 {{ detail?.metrics?.comment ?? '—' }}</p>
        <div v-if="comments.length">
          <div v-for="c in comments" :key="c.comment_id" class="comment">
            <div class="c-head">
              <b>{{ c.nickname || '匿名' }}</b>
              <span class="c-meta">{{ c.ip_location }} · 赞 {{ c.like_count }}<template v-if="fmtTime(c.created_at)"> · {{ fmtTime(c.created_at) }}</template></span>
            </div>
            <div class="c-body">{{ c.content }}</div>
          </div>
          <el-button v-if="comments.length < commentTotal" text @click="loadMoreComments">加载更多</el-button>
        </div>
        <p v-else class="comment-hint">
          该作品评论数或点赞数未达抓取门槛，仅显示数量。
        </p>

        <el-divider>分档加工</el-divider>
        <div class="tier-row">
          <el-tag size="small" type="info">当前档位：{{ tierLabel(detail.status?.tier) }}</el-tag>
          <el-tag size="small" :type="t(proc.understand_status)">理解 {{ sStatus(proc.understand_status) }}</el-tag>
        </div>
        <div class="tier-actions">
          <el-button size="small" :loading="procBusy" :disabled="procBusy" @click="runProcess('basic')">转录（普通）</el-button>
          <el-button size="small" type="primary" :loading="procBusy" :disabled="procBusy" @click="runProcess('advanced')">视频理解（高级）</el-button>
          <el-checkbox v-model="proc.use_cover">判类用封面</el-checkbox>
          <el-select v-model="proc.force_category" placeholder="改类型重跑（可选）" clearable size="small" style="width: 180px">
            <el-option v-for="(label, key) in taxonomy" :key="key" :label="label" :value="key" />
          </el-select>
          <el-button size="small" :loading="procBusy" :disabled="!proc.force_category || procBusy" @click="runReclassify">按类型重跑</el-button>
        </div>

        <div v-if="understanding" class="understand">
          <p>
            <b>判类：</b>
            <el-tag size="small" type="success">{{ taxonomy[understanding.category] || understanding.category }}</el-tag>
            <span class="c-meta">置信度 {{ (understanding.category_confidence * 100).toFixed(0) }}%</span>
            <el-tag v-if="understanding.low_confidence" size="small" type="warning">低置信度</el-tag>
          </p>
          <p class="c-meta">{{ understanding.category_reason }}</p>
          <p v-if="understanding.summary"><b>概述：</b>{{ understanding.summary }}</p>
          <div v-if="understanding.dimensions && Object.keys(understanding.dimensions).length" class="dims">
            <div v-for="(v, k) in understanding.dimensions" :key="k" class="dim">
              <span class="dim-k">{{ dimensionName(k) }}</span>
              <span class="dim-v">{{ fmtVal(v) }}</span>
            </div>
          </div>
          <p v-if="understanding.timeline?.length" class="c-meta">
            时间线：<template v-for="(e, i) in understanding.timeline" :key="i">{{ e.t }}s {{ e.event }}<template v-if="i < understanding.timeline.length - 1"> · </template></template>
          </p>
          <p class="c-meta">模型 {{ understanding.model }} · 版本 {{ understanding.prompt_version }}</p>
          <p v-if="understanding.error" class="err">{{ understanding.error }}</p>
        </div>
        <p v-else-if="proc.understand_status === 'error'" class="err">理解失败：{{ understandError || detail.status?.error }}</p>

        <el-divider>爆款拆解</el-divider>
        <div class="bd-actions">
          <el-button size="small" type="primary" :loading="bdBusy" :disabled="bdBusy || isBdActive()" @click="runBreakdown(!!breakdown)">
            {{ breakdown ? '重新拆解' : '生成爆款拆解' }}
          </el-button>
          <el-tag v-if="isBdActive()" size="small" type="warning">拆解中…</el-tag>
          <el-tag v-else-if="bdStatus === 'error'" size="small" type="danger">失败</el-tag>
          <span class="c-meta">复用转录 / 视频理解 / 评论 / 指标，产出「为什么火 + 怎么复刻」</span>
        </div>
        <p v-if="bdStatus === 'error' && bdError" class="err">{{ bdError }}</p>

        <div v-if="breakdown" class="bd-report">
          <div class="bd-head">
            <el-progress type="dashboard" :percentage="Math.round(breakdown.score || 0)" :width="96" />
            <div class="bd-verdict">
              <p class="bd-verdict-text">{{ breakdown.verdict || '（无结论）' }}</p>
              <p class="c-meta">
                模型 {{ breakdown.model || '—' }} · 提示词 {{ breakdown.prompt_version || '—' }}
                <el-tag v-if="breakdown.understanding_available === false" size="small" type="warning">无视频理解（降级报告）</el-tag>
              </p>
            </div>
          </div>

          <div v-if="breakdown.sections?.hook" class="bd-block">
            <h4>开场钩子</h4>
            <p v-if="breakdown.sections.hook.first_line"><b>首句台词：</b>{{ breakdown.sections.hook.first_line }}</p>
            <p v-if="breakdown.sections.hook.first_frame"><b>首帧画面：</b>{{ breakdown.sections.hook.first_frame }}</p>
            <p>
              <el-tag v-if="breakdown.sections.hook.hook_type" size="small">{{ breakdown.sections.hook.hook_type }}</el-tag>
              <span class="c-meta">{{ breakdown.sections.hook.why }}</span>
            </p>
          </div>

          <div v-if="breakdown.sections?.structure?.length" class="bd-block">
            <h4>内容结构</h4>
            <el-table :data="breakdown.sections.structure" size="small" border>
              <el-table-column prop="segment" label="时间段" width="110" />
              <el-table-column prop="role" label="作用" width="90" />
              <el-table-column prop="summary" label="概述" show-overflow-tooltip />
            </el-table>
          </div>

          <div v-if="breakdown.sections?.pacing" class="bd-block">
            <h4>节奏</h4>
            <p v-if="breakdown.sections.pacing.emotion_curve"><b>情绪曲线：</b>{{ breakdown.sections.pacing.emotion_curve }}</p>
            <p v-if="breakdown.sections.pacing.pattern_interrupts?.length">
              <b>变化点：</b>
              <template v-for="(x, i) in breakdown.sections.pacing.pattern_interrupts" :key="i">{{ x }}<template v-if="i < breakdown.sections.pacing.pattern_interrupts.length - 1">；</template></template>
            </p>
            <p v-if="breakdown.sections.pacing.re_engagement_points?.length">
              <b>二次拉回：</b>{{ breakdown.sections.pacing.re_engagement_points.join('、') }} 秒
            </p>
          </div>

          <div v-if="breakdown.sections?.script" class="bd-block">
            <h4>文案</h4>
            <p v-if="breakdown.sections.script.money_lines?.length"><b>金句：</b>{{ breakdown.sections.script.money_lines.join('；') }}</p>
            <p v-if="breakdown.sections.script.reusable_phrases?.length"><b>可复用句式：</b>{{ breakdown.sections.script.reusable_phrases.join('；') }}</p>
          </div>

          <div v-if="breakdown.sections?.psych_triggers?.length" class="bd-block">
            <h4>心理触发</h4>
            <el-tag v-for="(p, i) in breakdown.sections.psych_triggers" :key="i" size="small" style="margin-right: 6px">{{ p }}</el-tag>
          </div>

          <div v-if="breakdown.sections?.cta" class="bd-block">
            <h4>行动号召（CTA）</h4>
            <p>
              <el-tag v-if="breakdown.sections.cta.type" size="small">{{ breakdown.sections.cta.type }}</el-tag>
              <span v-if="breakdown.sections.cta.placement" class="c-meta">位置：{{ breakdown.sections.cta.placement }}</span>
            </p>
          </div>

          <div v-if="hasCommentInsights" class="bd-block">
            <h4>评论洞察</h4>
            <p v-if="breakdown.comment_insights.top_themes?.length"><b>热门话题：</b>{{ breakdown.comment_insights.top_themes.join('；') }}</p>
            <p v-if="breakdown.comment_insights.controversies?.length"><b>争议点：</b>{{ breakdown.comment_insights.controversies.join('；') }}</p>
            <p v-if="breakdown.comment_insights.questions?.length"><b>可回复问题：</b>{{ breakdown.comment_insights.questions.join('；') }}</p>
          </div>

          <div v-if="breakdown.data_context" class="bd-block">
            <h4>数据上下文 <span class="c-meta">（后端确定性计算）</span></h4>
            <div class="bd-metrics">
              <div class="bd-metric"><span class="dim-k">播放</span><span>{{ fmtNum(breakdown.data_context.play) }}</span></div>
              <div class="bd-metric"><span class="dim-k">点赞</span><span>{{ fmtNum(breakdown.data_context.like) }}</span></div>
              <div class="bd-metric"><span class="dim-k">评论</span><span>{{ fmtNum(breakdown.data_context.comment) }}</span></div>
              <div class="bd-metric"><span class="dim-k">播放 / 中位数</span><span>{{ fmtMult(breakdown.data_context.play_vs_median) }}</span></div>
              <div class="bd-metric"><span class="dim-k">点赞 / 中位数</span><span>{{ fmtMult(breakdown.data_context.like_vs_median) }}</span></div>
              <div class="bd-metric"><span class="dim-k">互动率</span><span>{{ fmtPct(breakdown.data_context.engagement_rate) }}</span></div>
              <div class="bd-metric"><span class="dim-k">分位</span><span>{{ fmtPct(breakdown.data_context.percentile, 0) }}</span></div>
            </div>
          </div>

          <div v-if="breakdown.sections?.blueprint?.length" class="bd-block">
            <h4>复刻蓝图</h4>
            <ol class="bd-list"><li v-for="(x, i) in breakdown.sections.blueprint" :key="i">{{ x }}</li></ol>
          </div>

          <div v-if="breakdown.sections?.replication_tips?.length" class="bd-block">
            <h4>落地建议</h4>
            <ul class="bd-list"><li v-for="(x, i) in breakdown.sections.replication_tips" :key="i">{{ x }}</li></ul>
          </div>
        </div>
        <el-empty v-else-if="!isBdActive() && bdStatus !== 'error'" description="尚无拆解报告" :image-size="60" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import PlatformMark from '../components/PlatformMark.vue'
import { api } from '../api'

const route = useRoute()

const assets = ref<any[]>([])
const selected = ref<any[]>([])
const loading = ref(false)
const drawer = ref(false)
const detail = ref<any>(null)
const filters = reactive({
  platform: '',
  account_id: null as number | null,
  transcribe_status: '',
  content_type: '',
  category: '',
})
const accounts = ref<Array<{ id: number; platform: string; display_name: string }>>([])
const filteredAccounts = computed(() =>
  accounts.value.filter((a) => !filters.platform || a.platform === filters.platform),
)

async function loadAccounts() {
  const { data } = await api.get('/api/accounts')
  accounts.value = data
}

/** 封面加载失败的素材 id：失败后退回类型占位符，不再显示破图。 */
const failedCovers = reactive(new Set<string>())
function coverFailed(id: string) { return failedCovers.has(id) }
function markCoverFailed(id: string) { failedCovers.add(id) }

const typeTabs = [
  { k: '', label: '全部' },
  { k: 'video', label: '视频' },
  { k: 'article', label: '文章' },
]
function setType(k: string) {
  filters.content_type = k
  load()
}

// 卡片选集：默认只在已选或已有勾选时显示勾选框
const pickMode = ref(false)
function isPicked(a: any) {
  return selected.value.some((x) => x.asset_id === a.asset_id)
}
function togglePick(a: any) {
  const i = selected.value.findIndex((x) => x.asset_id === a.asset_id)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(a)
}

// 视频播放 / 文章阅读
const player = ref<any>(null)
const article = ref<any>(null)
function openPlayer(a: any) {
  player.value = a
}
async function openArticle(assetId: number) {
  const { data } = await api.get(`/api/assets/${assetId}/article`)
  article.value = data
}

// 封面即入口：视频点开播放（需已下载），文章点开阅读（有正文即可）。
function canOpen(a: any) {
  return a.content_type === 'video' ? !!a.playable : true
}
function coverHint(a: any) {
  if (a.content_type === 'video') return a.playable ? '点击封面播放' : '未下载，无法播放'
  return '点击封面阅读'
}
function onCoverClick(a: any) {
  if (!canOpen(a)) return
  if (a.content_type === 'video') openPlayer(a)
  else openArticle(a.asset_id)
}
// 已转录 / 已分析：决定按钮显示「转录」还是「重新转录」。
function isTranscribed(a: any) {
  return a.transcribe_status === 'done'
}
function isAnalyzed(a: any) {
  return a.analyze_status === 'done'
}

const importDialog = ref(false)
const importUrl = ref('')
const importing = ref(false)

const comments = ref<any[]>([])
const commentTotal = ref(0)
const commentOffset = ref(0)

// 分档加工
const proc = reactive<any>({ understand_status: '', use_cover: false, force_category: '' })
const understanding = ref<any>(null)
const understandError = ref('')
const taxonomy = ref<Record<string, string>>({})
const commonDimensions = ref<any[]>([])
const dimensionNames = ref<Record<string, string>>({})
// 「判类默认用封面」全局默认值（来自设置页 process_use_cover），
// 抽屉打开时用它初始化勾选框，否则设置页开关没有消费方（回归修复）。
const globalUseCover = ref(false)
const procBusy = ref(false)

let procTimer: number | undefined

// 爆款拆解：报告 / 状态 / 轮询。
const breakdown = ref<any>(null)
const bdStatus = ref('')
const bdError = ref('')
const bdBusy = ref(false)
// 本次会话是否已点过「生成/重新拆解」。GET 对「从未生成」也返回 pending，
// 靠它区分「排队中」与「尚未生成」，避免未请求就显示「拆解中」（UX 修正）。
const bdRequested = ref(false)
let bdTimer: number | undefined

// 加工中状态：需轮询等待 worker 完成。
const ACTIVE_STATUS = ['pending', 'doing']
function isProcActive() {
  return ACTIVE_STATUS.includes(proc.understand_status)
}
function stopProcPoll() {
  if (procTimer) { clearInterval(procTimer); procTimer = undefined }
}
function syncProcTimer() {
  if (drawer.value && detail.value && isProcActive()) {
    if (!procTimer) procTimer = window.setInterval(() => loadProcess(detail.value.asset_id), 3000)
  } else {
    stopProcPoll()
  }
}

// 是否在途：本地已请求（pending/doing）或服务端明确 doing 才轮询。
function isBdActive() {
  if (bdStatus.value === 'doing') return true
  return bdRequested.value && bdStatus.value === 'pending'
}
function stopBdPoll() {
  if (bdTimer) { clearInterval(bdTimer); bdTimer = undefined }
}
function syncBdTimer() {
  if (drawer.value && detail.value && isBdActive()) {
    if (!bdTimer) bdTimer = window.setInterval(() => loadBreakdown(detail.value.asset_id), 3000)
  } else {
    stopBdPoll()
  }
}

const procStatusText: Record<string, string> = {
  pending: '待处理', doing: '进行中', done: '完成', error: '失败',
}
function sStatus(v: string) { return procStatusText[v] ?? (v || '—') }

function dimensionName(key: string) {
  return dimensionNames.value[key] || key
}
function fmtVal(v: any) {
  if (v === null || v === undefined || v === '') return '—'
  return typeof v === 'object' ? JSON.stringify(v, null, 0) : String(v)
}

// 档位取值 -> 中文（两档：basic 普通 / advanced 高级）。
const tierNames: Record<string, string> = { basic: '普通（转录）', advanced: '高级（视频理解）' }
function tierLabel(tier: string | undefined) {
  return tier ? (tierNames[tier] ?? tier) : '未加工'
}

// 维度 key -> 中文名：合并通用维度 + 各类型专属维度。
function buildDimensionNames(common: any[], prompts: Record<string, any>) {
  const names: Record<string, string> = {}
  for (const d of common || []) if (d?.key) names[d.key] = d.name
  for (const spec of Object.values(prompts || {})) {
    for (const d of (spec as any)?.dimensions || []) if (d?.key) names[d.key] = d.name
  }
  dimensionNames.value = names
}

async function loadConfig(force = false) {
  if (!force && commonDimensions.value.length) return
  try {
    const { data } = await api.get('/api/system/process-settings')
    taxonomy.value = data.taxonomy || {}
    commonDimensions.value = data.common_dimensions || []
    buildDimensionNames(commonDimensions.value, data.type_prompts || {})
    globalUseCover.value = !!data.use_cover
  } catch { /* 设置不可用不阻塞详情 */ }
}

async function loadProcess(assetId: number, opts: { resetInputs?: boolean } = {}) {
  await loadConfig(opts.resetInputs)
  if (opts.resetInputs) {
    proc.use_cover = globalUseCover.value
    proc.force_category = ''
  }
  understanding.value = null
  understandError.value = ''
  const u = await api.get(`/api/assets/${assetId}/understanding`)
  proc.understand_status = u.data.understand_status
  if (detail.value?.status && u.data.process_tier) detail.value.status.tier = u.data.process_tier
  understanding.value = u.data.status === 'pending' ? null : u.data
  understandError.value = u.data.error || ''
  syncProcTimer()
}

async function runProcess(tier: string) {
  if (!detail.value || procBusy.value) return
  procBusy.value = true
  try {
    await api.post(`/api/assets/${detail.value.asset_id}/process`, {
      tier,
      force_category: proc.force_category || undefined,
      use_cover: proc.use_cover,
    })
    ElMessage.success('已加入队列')
    await loadProcess(detail.value.asset_id)
  } finally {
    procBusy.value = false
  }
}

async function runReclassify() {
  if (!detail.value || !proc.force_category || procBusy.value) return
  procBusy.value = true
  try {
    await api.post(`/api/assets/${detail.value.asset_id}/reclassify`, {
      force_category: proc.force_category,
      use_cover: proc.use_cover,
    })
    ElMessage.success('已按新类型重跑')
    await loadProcess(detail.value.asset_id)
  } finally {
    procBusy.value = false
  }
}

// ---- 爆款拆解 ----
const hasCommentInsights = computed(() => {
  const ci = breakdown.value?.comment_insights
  if (!ci) return false
  return ['top_themes', 'controversies', 'questions'].some((k) => ci[k]?.length)
})

async function loadBreakdown(assetId: number) {
  const { data } = await api.get(`/api/assets/${assetId}/breakdown`)
  bdStatus.value = data.status
  if (data.status === 'done') {
    breakdown.value = data
    bdError.value = ''
  } else {
    breakdown.value = null
    bdError.value = data.error || ''
  }
  syncBdTimer()
}

async function runBreakdown(force: boolean) {
  if (!detail.value || bdBusy.value) return
  bdBusy.value = true
  bdRequested.value = true
  try {
    await api.post(`/api/assets/${detail.value.asset_id}/breakdown`, { force })
    ElMessage.success('已加入拆解队列')
    await loadBreakdown(detail.value.asset_id)
  } finally {
    bdBusy.value = false
  }
}

function fmtNum(v: any) {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('zh-CN')
}
function fmtMult(v: any) {
  if (!v) return '—'
  return `${Number(v).toFixed(2)}×`
}
function fmtPct(v: any, digits = 1) {
  if (v === null || v === undefined) return '—'
  return `${(Number(v) * 100).toFixed(digits)}%`
}

async function doImport() {
  if (!importUrl.value.trim()) return
  importing.value = true
  try {
    await api.post('/api/imports/wechat-article', { url: importUrl.value.trim() })
    ElMessage.success('导入成功')
    importDialog.value = false
    importUrl.value = ''
    await load()
  } finally {
    importing.value = false
  }
}

const statusText: Record<string, string> = {
  pending: '待处理', downloading: '下载中', transcribing: '转录中', analyzing: '分析中', done: '完成', error: '失败',
}
function s(v: string) { return statusText[v] ?? v }
function t(v: string) { return ({ done: 'success', error: 'danger' } as Record<string, string>)[v] ?? 'info' }

// 卡片展示用格式化
const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音', bilibili: 'B站', kuaishou: '快手', zhihu: '知乎',
  xhs: '小红书', wechat_mp: '微信公众号',
}
function platformLabel(p: string) {
  return PLATFORM_LABELS[p] || p || '未知'
}
const TYPE_LABELS: Record<string, string> = { video: '视频', article: '文章', image: '图文' }
function typeLabel(t: string) {
  return TYPE_LABELS[t] || t || '内容'
}
function statusTone(v: string) {
  return ({ done: 'ok', error: 'bad' } as Record<string, string>)[v] || ''
}
function mmss(sec: number) {
  const s = Math.max(0, Math.round(sec))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
function compact(v: any) {
  const n = Number(v || 0)
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`
  return String(n)
}
function bytes(v: any) {
  const n = Number(v || 0)
  if (n <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let x = n
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i += 1 }
  return `${x.toFixed(i >= 3 ? 2 : 0)} ${units[i]}`
}
function fmtDate(v: string | null) {
  if (!v) return '—'
  const d = new Date(v.endsWith('Z') ? v : v + 'Z')
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}
// 分类中文名（来自系统设置的类型目录）
async function loadTaxonomy() {
  try {
    const { data } = await api.get('/api/system/process-settings')
    taxonomy.value = data.taxonomy || {}
  } catch { /* 不阻塞列表 */ }
}
// 评论时间：后端为 UTC naive ISO（无 Z），补 'Z' 再按本地时区显示。
function fmtTime(v: string | null) {
  if (!v) return ''
  return new Date(v.endsWith('Z') ? v : v + 'Z').toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/api/assets', {
      params: { ...filters, limit: 200 },
    })
    assets.value = data
  } finally {
    loading.value = false
  }
}

async function openDetail(id: number) {
  const { data } = await api.get(`/api/assets/${id}`)
  detail.value = data
  drawer.value = true
  await loadComments(id)
  await loadProcess(id, { resetInputs: true })
  breakdown.value = null
  bdStatus.value = ''
  bdError.value = ''
  bdRequested.value = false
  await loadBreakdown(id)
}

function closeDrawer() {
  stopProcPoll()
  stopBdPoll()
}

async function loadComments(assetId: number, reset = true) {
  if (reset) {
    comments.value = []
    commentOffset.value = 0
  }
  const { data } = await api.get(`/api/assets/${assetId}/comments`, {
    params: { limit: 50, offset: commentOffset.value },
  })
  comments.value = reset ? data.comments : [...comments.value, ...data.comments]
  commentTotal.value = data.total
  commentOffset.value = comments.value.length
}

async function loadMoreComments() {
  if (detail.value) await loadComments(detail.value.asset_id, false)
}

async function act(id: number, kind: string) {
  await api.post(`/api/assets/${id}/${kind}`)
  ElMessage.success('已加入队列')
  await load()
}

async function exportSel(format: string) {
  const ids = selected.value.map((a) => a.asset_id)
  const { data } = await api.post('/api/assets/export', { asset_ids: ids, format })
  const content = format === 'json' ? JSON.stringify(data, null, 2) : data.content
  const blob = new Blob([content], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `assets.${format}`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  const qPlatform = String(route.query.platform || '')
  const qAccount = Number(route.query.account_id || 0)
  if (qPlatform) filters.platform = qPlatform
  if (qAccount) filters.account_id = qAccount
  loadAccounts()
  loadTaxonomy()
  load()
})
onUnmounted(() => {
  stopProcPoll()
  stopBdPoll()
})
</script>

<style scoped>
.ui-page { display: flex; flex-direction: column; gap: var(--sp-5); }
.ui-filters { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.ui-filters .grow { margin-left: auto; }

/* 素材卡片网格 */
.asset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(272px, 1fr)); gap: var(--sp-3); }
.asset {
  background: var(--surface);
  border: 1px solid var(--ink);
  box-shadow: var(--shadow-1);
  display: flex;
  flex-direction: column;
  transition: transform .14s ease-out, box-shadow .14s ease-out;
}
.asset:hover { transform: translate(-1px, -1px); box-shadow: var(--shadow-2); }
.asset.picked { outline: 3px solid var(--accent); outline-offset: -3px; }

.asset .cover {
  position: relative;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  display: grid;
  place-items: center;
  background-color: var(--surface-3);
  background-size: cover;
  background-position: center;
  border-bottom: 1px solid var(--ink);
  display: grid;
  place-items: center;
}
.asset .cover .ph { font-size: var(--fs-2xl); font-weight: 800; color: var(--muted); letter-spacing: .08em; }
.asset .cover > img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.asset .cover .badge-plat,
.asset .cover .badge-type,
.asset .cover .badge-dur,
.asset .cover .badge-nofile {
  position: absolute;
  font-size: var(--fs-xs);
  font-weight: 600;
  padding: 2px 7px;
  border: 1px solid var(--ink);
  background: var(--surface);
  color: var(--ink);
}
.asset .cover .badge-plat { top: 8px; left: 8px; background: var(--ink); color: var(--surface); }
.asset .cover .badge-type { top: 8px; right: 8px; background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.asset .cover .badge-dur { bottom: 8px; right: 8px; font-variant-numeric: tabular-nums; }
.asset .cover .badge-nofile { bottom: 8px; right: 8px; color: var(--muted); }
/* 封面即入口：整块可点，hover 时浮出「播放 / 阅读」提示 */
.asset .cover.clickable { cursor: pointer; }
.asset .cover.clickable > img { transition: transform .18s ease-out, filter .18s ease-out; }
.asset .cover.clickable:hover > img { transform: scale(1.04); filter: brightness(.82); }
.asset .cover .cover-cta {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center; gap: 8px;
  font-size: var(--fs-lg); font-weight: 750; color: #fff;
  text-shadow: 0 1px 6px rgba(0,0,0,.6);
  opacity: 0; transition: opacity .16s ease-out;
  pointer-events: none;
}
.asset .cover.clickable:hover .cover-cta { opacity: 1; }
.asset .cover .cover-cta .cta-ic {
  display: grid; place-items: center;
  width: 48px; height: 48px; border-radius: 50%;
  border: 2px solid #fff; background: rgba(0,0,0,.28);
  font-size: 20px;
}
.asset .body { padding: var(--sp-4); display: flex; flex-direction: column; gap: 9px; flex: 1; }
.asset .title {
  font-weight: 650; font-size: var(--fs-md); color: var(--ink); line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.asset .author { display: flex; align-items: center; gap: 7px; min-width: 0; }
.asset .ava {
  width: 22px; height: 22px; flex: none;
  background-color: var(--surface-2); background-size: cover; background-position: center;
  border: 1px solid var(--ink);
  display: grid; place-items: center;
  font-size: var(--fs-xs); font-weight: 700; color: var(--ink-2);
  overflow: hidden;
}
.asset .aname {
  color: var(--ink-2); font-size: var(--fs-sm); text-decoration: none;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.asset .aname:hover { color: var(--accent); text-decoration: underline; }

.asset .meta-rows { display: flex; flex-direction: column; gap: 3px; }
.asset .mrow { display: flex; align-items: center; gap: 7px; font-size: var(--fs-xs); }
.asset .mk { color: var(--muted); min-width: 28px; }
.asset .mv { color: var(--ink-2); font-variant-numeric: tabular-nums; }
.ui-badge.none { border-style: dashed; color: var(--muted); background: transparent; }
.ui-badge em { font-style: normal; opacity: .75; }

.asset .nums {
  display: flex; flex-wrap: wrap; gap: 4px 10px;
  font-size: var(--fs-xs); color: var(--muted);
  border-top: 1px dashed var(--line-soft);
  padding-top: 8px;
}
.asset .nums b { color: var(--ink); font-variant-numeric: tabular-nums; }

.asset .foot { display: flex; align-items: center; gap: 8px; font-size: var(--fs-xs); color: var(--muted); }
.asset .foot .grow { margin-left: auto; }
.asset .size { font-variant-numeric: tabular-nums; }
.asset .pick input { width: 16px; height: 16px; accent-color: var(--accent); cursor: pointer; }

.asset .acts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: auto; }

/* 播放器 / 文章阅读 */
.ui-modal {
  position: fixed; inset: 0; z-index: 200;
  background: color-mix(in oklab, var(--ink) 55%, transparent);
  display: grid; place-items: center; padding: var(--sp-5);
}
.ui-modal-box {
  width: min(920px, 100%); max-height: 90vh; display: flex; flex-direction: column;
  background: var(--surface); border: 2px solid var(--ink); box-shadow: var(--shadow-2);
}
.player-wrap { background: oklch(0.12 0 0); display: grid; place-items: center; }
.player-wrap video { width: 100%; max-height: 64vh; display: block; }
.player-meta { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: var(--sp-3) var(--sp-4); font-size: var(--fs-sm); color: var(--muted); }
.article-body { padding: var(--sp-5); overflow: auto; }
.art-meta { color: var(--muted); font-size: var(--fs-sm); margin: 0 0 14px; }
.art-html { line-height: 1.8; font-size: var(--fs-base); color: var(--ink-2); }
.art-html :deep(img) { max-width: 100%; height: auto; border: 1px solid var(--line-soft); }
.art-html :deep(p) { margin: 0 0 12px; }
.art-text { white-space: pre-wrap; font-family: var(--font-sans); line-height: 1.8; color: var(--ink-2); margin: 0; }

.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.meta { color: #909399; margin: 4px 0 12px; }
.hint { color: #909399; font-size: 12px; margin: 8px 0 0; }
.comment { padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.c-head { display: flex; justify-content: space-between; }
.c-meta { color: #909399; font-size: 12px; }
.c-body { margin-top: 4px; font-size: 13px; }
.comment-total { color: #606266; font-size: 12px; margin: 0 0 4px; }
.comment-hint { color: #909399; font-size: 12px; }
.tier-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tier-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 10px 0; }
.understand { background: #fafafa; border: 1px solid #f0f0f0; border-radius: 6px; padding: 10px; font-size: 13px; }
.dims { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.dim { display: flex; gap: 8px; }
.dim-k { color: #909399; min-width: 88px; }
.dim-v { flex: 1; }
.err { color: #f56c6c; font-size: 12px; }

/* 爆款拆解 */
.bd-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.bd-report { margin-top: 12px; }
.bd-head { display: flex; gap: 16px; align-items: center; }
.bd-verdict { flex: 1; }
.bd-verdict-text { font-size: 15px; font-weight: 600; margin: 0 0 6px; }
.bd-block { margin-top: 14px; }
.bd-block h4 { margin: 0 0 6px; font-size: 13px; color: #303133; }
.bd-block p { margin: 4px 0; font-size: 13px; }
.bd-metrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 16px; font-size: 13px; }
.bd-metric { display: flex; gap: 8px; }
.bd-list { margin: 4px 0; padding-left: 20px; font-size: 13px; }
.bd-list li { margin: 2px 0; }
</style>
