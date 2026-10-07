<template>
  <span class="av" :style="style">
    <!-- 首字母垫在底层：图片加载成功会盖住它，失败则露出，避免出现空白方块 -->
    <span class="av-fallback">{{ initial }}</span>
    <img v-if="src && !failed" :src="src" alt="" loading="lazy" @error="failed = true" />
  </span>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  src?: string | null
  name?: string | null
  /** 边长，px */
  size?: number
}>()

const failed = ref(false)
// 换人/换图时重置失败态，否则复用组件会一直显示占位
watch(() => props.src, () => { failed.value = false })

const initial = computed(() => {
  const n = (props.name || '').trim()
  return n ? n.slice(0, 1) : '?'
})

const style = computed(() => ({
  width: `${props.size ?? 40}px`,
  height: `${props.size ?? 40}px`,
  fontSize: `${Math.max(11, Math.round((props.size ?? 40) * 0.42))}px`,
}))
</script>

<style scoped>
.av {
  position: relative;
  display: inline-grid;
  place-items: center;
  flex: none;
  overflow: hidden;
  background: var(--surface-3);
  border: 1px solid var(--ink);
  color: var(--muted);
  font-weight: 700;
  line-height: 1;
  user-select: none;
}
.av-fallback { grid-area: 1 / 1; }
.av img {
  grid-area: 1 / 1;
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
</style>
