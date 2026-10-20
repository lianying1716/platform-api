<template>
  <div ref="host" class="echart-host">
    <v-chart
      v-if="ready"
      class="echart"
      :option="option"
      :update-options="{ notMerge: true }"
      autoresize
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import { ensureCoreCharts } from './echarts-registry'

// Dashboard pages only need the core chart set; the registry lets the design
// sample opt into the rest without shipping them to every visitor.
ensureCoreCharts()

const props = defineProps<{
  /** 图表配置工厂：接收主题色令牌，返回 ECharts option */
  build: (t: Record<string, string>) => Record<string, unknown>
}>()

const host = ref<HTMLElement>()
const tokens = ref<Record<string, string>>({})
const ready = ref(false)

const TOKEN_NAMES = [
  '--ink', '--ink-2', '--muted', '--line', '--line-soft', '--surface', '--surface-2',
  '--accent', '--accent-2', '--accent-soft', '--ok', '--ok-soft', '--warn', '--warn-soft',
  '--danger', '--danger-soft', '--chart-grid',
]

/** 从当前作用域读设计令牌，让图表跟随方向主题。 */
function readTokens() {
  const el = (host.value?.closest('[data-dir]') as HTMLElement) || document.documentElement
  const cs = getComputedStyle(el)
  const out: Record<string, string> = {}
  for (const n of TOKEN_NAMES) out[n] = cs.getPropertyValue(n).trim()
  tokens.value = out
  // 令牌齐全（至少主线颜色可解析）才渲染图表，避免用 undefined 颜色绘制
  ready.value = Boolean(out['--ink'] && out['--accent'])
}

const option = computed(() => props.build(tokens.value))

onMounted(() => {
  readTokens()
  const el = (host.value?.closest('[data-dir]') as HTMLElement) || document.documentElement
  new MutationObserver(() => readTokens()).observe(el, {
    attributes: true,
    attributeFilter: ['data-dir'],
  })
})
</script>

<style scoped>
.echart-host {
  width: 100%;
  height: 100%;
  /* grid/flex 子项默认 min-width:auto，canvas 会把容器越撑越宽，必须归零 */
  min-width: 0;
  overflow: hidden;
}
.echart {
  width: 100%;
  height: 100%;
  min-height: 200px;
}
</style>
