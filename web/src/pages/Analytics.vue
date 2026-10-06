<template>
  <div>
    <el-tabs v-model="tab">
      <el-tab-pane label="作者维度" name="creators">
        <el-table :data="creators" v-loading="loading" stripe row-class-name="rp-row" @row-click="openReport">
          <el-table-column label="作者" min-width="160">
            <template #default="{ row }">{{ row.author_name || row.author_id }}</template>
          </el-table-column>
          <el-table-column prop="platform" label="平台" width="110" />
          <el-table-column prop="content_count" label="内容数" width="120" sortable />
          <el-table-column label="操作" width="140">
            <template #default="{ row }">
              <el-button size="small" type="primary" plain @click.stop="openReport(row)">对标账号拆解</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="关键词维度" name="keywords">
        <el-table :data="keywords" v-loading="loading" stripe>
          <el-table-column prop="task_id" label="采集任务 ID" width="140" />
          <el-table-column prop="total_added" label="累计新增" width="140" sortable />
          <el-table-column prop="runs" label="运行次数" width="120" />
        </el-table>
      </el-tab-pane>
    </el-tabs>
    <p class="tip">趋势走势依赖每次抓取存下的指标快照，运行采集/同步任务后数据会逐步积累。</p>

    <el-dialog v-model="reportDialog" title="对标账号拆解" width="820px" top="6vh">
      <div v-if="report" v-loading="reportLoading">
        <h3 class="rp-title">
          {{ report.author_name || report.author_id }}
          <span class="rp-meta"> · {{ report.platform }}</span>
        </h3>

        <el-divider>账号概览</el-divider>
        <div class="rp-metrics">
          <div class="rp-metric"><span class="rp-k">内容数</span><span>{{ report.overview.content_count }}</span></div>
          <div class="rp-metric"><span class="rp-k">有快照</span><span>{{ report.overview.snapshot_count }}</span></div>
          <div class="rp-metric"><span class="rp-k">平均播放</span><span>{{ fmtNum(report.overview.avg_play) }}</span></div>
          <div class="rp-metric"><span class="rp-k">中位播放</span><span>{{ fmtNum(report.overview.median_play) }}</span></div>
          <div class="rp-metric"><span class="rp-k">平均点赞</span><span>{{ fmtNum(report.overview.avg_like) }}</span></div>
          <div class="rp-metric"><span class="rp-k">平均评论</span><span>{{ fmtNum(report.overview.avg_comment) }}</span></div>
          <div class="rp-metric"><span class="rp-k">平均互动率</span><span>{{ fmtPct(report.overview.avg_engagement_rate) }}</span></div>
          <div v-if="timespan" class="rp-metric"><span class="rp-k">时间跨度</span><span>{{ timespan }}</span></div>
        </div>

        <el-divider>发布节奏</el-divider>
        <p class="rp-meta">按星期：</p>
        <div class="rp-bars">
          <div v-for="w in report.cadence.by_weekday" :key="w.weekday" class="rp-bar">
            <span class="rp-bar-label">{{ w.weekday }}</span>
            <el-progress :percentage="pctOf(w.count, maxWeekday)" :show-text="false" :stroke-width="10" />
            <span class="rp-bar-val">{{ w.count }}</span>
          </div>
        </div>
        <p class="rp-meta" style="margin-top: 8px">按小时：</p>
        <div class="rp-bars">
          <div v-for="h in report.cadence.by_hour" :key="h.hour" class="rp-bar">
            <span class="rp-bar-label">{{ h.hour }}时</span>
            <el-progress :percentage="pctOf(h.count, maxHour)" :show-text="false" :stroke-width="10" />
            <span class="rp-bar-val">{{ h.count }}</span>
          </div>
        </div>
        <p v-if="report.cadence.best_hours?.length" class="rp-meta">
          高峰时段：{{ report.cadence.best_hours.map((h: number) => h + '时').join('、') }}
        </p>

        <el-divider>Top 作品</el-divider>
        <el-table :data="report.top" size="small" border max-height="260">
          <el-table-column label="标题" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <el-tag v-if="row.viral" size="small" type="danger" style="margin-right: 4px">爆</el-tag>
              <a v-if="row.url" :href="row.url" target="_blank">{{ row.title || '（无标题）' }}</a>
              <span v-else>{{ row.title || '（无标题）' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="110" prop="category" show-overflow-tooltip />
          <el-table-column label="播放" width="100"><template #default="{ row }">{{ fmtNum(row.play) }}</template></el-table-column>
          <el-table-column label="点赞" width="90"><template #default="{ row }">{{ fmtNum(row.like) }}</template></el-table-column>
          <el-table-column label="评论" width="80"><template #default="{ row }">{{ fmtNum(row.comment) }}</template></el-table-column>
        </el-table>

        <el-divider>爆款规律</el-divider>
        <p>判定：播放 ≥ 自身中位数 × {{ report.viral_rules.threshold_multiple }}（共 {{ report.viral_rules.count }} 条）</p>
        <p v-if="viralTypes.length" class="rp-meta">
          类型分布：<template v-for="(t, i) in viralTypes" :key="t.category">{{ t.category }} {{ t.count }}<template v-if="i < viralTypes.length - 1"> · </template></template>
        </p>
        <p class="rp-meta">
          平均时长 {{ report.viral_rules.avg_duration_sec.toFixed(1) }} 秒 · 平均播放 {{ fmtNum(report.viral_rules.avg_play) }}<template v-if="report.viral_rules.top_hours?.length"> · 高发时段 {{ report.viral_rules.top_hours.map((h: number) => h + '时').join('、') }}</template>
        </p>

        <el-divider>内容支柱</el-divider>
        <div v-for="p in report.content_pillars" :key="p.category" class="rp-bar">
          <span class="rp-bar-label">{{ p.category }}</span>
          <el-progress :percentage="Math.round(p.share * 100)" :stroke-width="12" />
        </div>

        <el-divider>对标基准</el-divider>
        <div class="rp-metrics">
          <div class="rp-metric"><span class="rp-k">本号中位播放</span><span>{{ fmtNum(report.benchmark.author_median_play) }}</span></div>
          <div class="rp-metric"><span class="rp-k">平台中位播放</span><span>{{ fmtNum(report.benchmark.platform_median_play) }}</span></div>
          <div class="rp-metric"><span class="rp-k">本号 / 平台</span><span>{{ fmtMult(report.benchmark.author_vs_platform) }}</span></div>
          <div class="rp-metric"><span class="rp-k">平台样本</span><span>{{ report.benchmark.platform_snapshot_count }} / {{ report.benchmark.platform_content_count }}</span></div>
        </div>

        <el-divider>缺口提示</el-divider>
        <div v-if="report.gaps?.length">
          <el-tag v-for="g in report.gaps" :key="g.category" size="small" type="warning" style="margin: 0 6px 6px 0">
            {{ g.category }}（平台中位 {{ fmtNum(g.platform_median_play) }}）
          </el-tag>
          <p class="rp-meta">平台高热度类型里，该账号尚未覆盖的题材。</p>
        </div>
        <el-empty v-else description="无可识别的内容缺口" :image-size="50" />
      </div>
      <template #footer>
        <el-button @click="reportDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'

const tab = ref('creators')
const creators = ref<any[]>([])
const keywords = ref<any[]>([])
const loading = ref(false)

const reportDialog = ref(false)
const reportLoading = ref(false)
const report = ref<any>(null)

const timespan = computed(() => {
  const t = report.value?.overview?.timespan
  if (!t?.start) return ''
  return `${fmtDate(t.start)} ~ ${fmtDate(t.end)}`
})
const maxWeekday = computed(() =>
  Math.max(1, ...(report.value?.cadence?.by_weekday || []).map((w: any) => w.count)),
)
const maxHour = computed(() =>
  Math.max(1, ...(report.value?.cadence?.by_hour || []).map((h: any) => h.count)),
)
const viralTypes = computed(() => {
  const d = report.value?.viral_rules?.type_distribution
  if (!d) return []
  return Object.entries(d).map(([category, count]) => ({ category, count }))
})

function fmtNum(v: any) {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
function fmtMult(v: any) {
  if (!v) return '—'
  return `${Number(v).toFixed(2)}×`
}
function fmtPct(v: any, digits = 1) {
  if (v === null || v === undefined) return '—'
  return `${(Number(v) * 100).toFixed(digits)}%`
}
function pctOf(v: number, max: number) {
  return max ? Math.round((v / max) * 100) : 0
}
function fmtDate(v: string) {
  return v ? v.slice(0, 10) : ''
}

async function openReport(row: any) {
  reportDialog.value = true
  reportLoading.value = true
  report.value = null
  try {
    const { data } = await api.get(
      `/api/analytics/creator/${encodeURIComponent(row.author_id)}/report`,
      { params: { platform: row.platform } },
    )
    report.value = data
  } catch {
    reportDialog.value = false
  } finally {
    reportLoading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    if (tab.value === 'creators') {
      creators.value = (await api.get('/api/analytics/creators')).data
    } else {
      keywords.value = (await api.get('/api/analytics/keywords')).data
    }
  } finally {
    loading.value = false
  }
}

watch(tab, load)
onMounted(load)
</script>

<style scoped>
.tip {
  color: #909399;
  font-size: 13px;
  margin-top: 12px;
}
:deep(.rp-row) { cursor: pointer; }
.rp-title { margin: 0 0 8px; font-size: 16px; }
.rp-meta { color: #909399; font-size: 12px; }
.rp-metrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px 20px; font-size: 13px; }
.rp-metric { display: flex; gap: 8px; }
.rp-k { color: #909399; min-width: 90px; }
.rp-bars { display: flex; flex-direction: column; gap: 4px; }
.rp-bar { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.rp-bar-label { min-width: 60px; color: #606266; }
.rp-bar-val { min-width: 28px; color: #909399; text-align: right; }
.rp-bar :deep(.el-progress) { flex: 1; }
</style>
