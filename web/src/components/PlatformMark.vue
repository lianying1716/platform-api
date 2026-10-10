<template>
  <span class="pm" :class="[`pm-${size}`, { muted, plain }]" :style="style">
    <span class="pm-badge">
      <svg v-if="meta.path" viewBox="0 0 24 24" aria-hidden="true">
        <path :d="meta.path" />
      </svg>
      <span v-else class="pm-fallback">{{ fallbackChar }}</span>
    </span>
    <span v-if="showLabel" class="pm-label">{{ meta.label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { PLATFORMS, platformLabel } from '../brand/platforms'

const props = withDefaults(
  defineProps<{
    /** 平台 slug，例如 douyin / bilibili / xhs / wechat_mp */
    platform: string | null | undefined
    /** 是否显示平台名文字 */
    showLabel?: boolean
    /** 尺寸档位 */
    size?: 'sm' | 'md' | 'lg'
    /** 弱化显示（例如列表里降低存在感） */
    muted?: boolean
    /** 是否用品牌色；false 则用当前文字色 */
    colored?: boolean
    /** 纯图标模式：去掉徽标底与描边（素材卡封面等已有底色的场合） */
    plain?: boolean
  }>(),
  { showLabel: true, size: 'md', muted: false, colored: true, plain: false },
)

// 未知平台：回退成中性标记，避免出现空白或裂图
const meta = computed(() => {
  const known = props.platform ? PLATFORMS[props.platform] : undefined
  if (known) return known
  return { label: platformLabel(props.platform), color: 'currentColor', path: '' }
})

const fallbackChar = computed(() => meta.value.label.replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, '').slice(0, 1) || '?')

const style = computed(() => {
  const c = meta.value.color
  return {
    '--pm-color': props.colored ? c : 'currentColor',
    // 用 8% 透明度做底，兼顾深浅背景
    '--pm-tint': color2rgba(c, 0.12),
  }
})

/** 把 #RRGGBB 转成 rgba()，用于徽标底色；非 hex 直接返回 transparent。 */
function color2rgba(hex: string, alpha: number) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex.trim())
  if (!m) return 'transparent'
  const [r, g, b] = [1, 2, 3].map((i) => parseInt(m[i], 16))
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}
</script>

<style scoped>
.pm {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--pm-color);
  line-height: 1;
  min-width: 0;
}
.pm.muted {
  color: color-mix(in oklab, var(--pm-color) 62%, var(--muted));
}
.pm-badge {
  display: grid;
  place-items: center;
  flex: none;
  background: var(--pm-tint);
  border: 1px solid color-mix(in oklab, var(--pm-color) 34%, transparent);
  color: var(--pm-color);
}
/* 纯图标模式：无底、无描边，图标直接坐落在卡片封面上 */
.pm.plain .pm-badge {
  background: none;
  border: 0;
  color: #fff;
  filter: drop-shadow(0 1px 3px rgba(0, 0, 0, .55));
}
.pm.plain .pm-badge svg { width: 100%; height: 100%; }
.pm-badge svg {
  width: 62%;
  height: 62%;
  fill: currentColor;
  display: block;
}
.pm-fallback {
  font-weight: 800;
  font-size: 0.72em;
}
.pm-label {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--ink-2);
}

.pm-sm .pm-badge { width: 20px; height: 20px; }
.pm-sm .pm-label { font-size: 12px; }
.pm-md .pm-badge { width: 26px; height: 26px; }
.pm-md .pm-label { font-size: 13px; }
.pm-lg .pm-badge { width: 34px; height: 34px; }
.pm-lg .pm-label { font-size: 15px; }
</style>
