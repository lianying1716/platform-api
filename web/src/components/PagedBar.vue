<template>
  <div v-if="totalPages > 1" class="ui-pager">
    <button class="ui-btn sm" :disabled="disabled || page <= 1" @click="emit('change', page - 1)">
      上一页
    </button>
    <span class="pager-info">
      第 {{ page }} / {{ totalPages }} 页 · 每页 {{ pageSize }} 条
    </span>
    <button class="ui-btn sm" :disabled="disabled || page >= totalPages" @click="emit('change', page + 1)">
      下一页
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  page: number
  pageSize: number
  total: number
  disabled?: boolean
}>(), {
  disabled: false,
})

const emit = defineEmits<{
  change: [page: number]
}>()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
</script>

<style scoped>
.ui-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-3);
}
.pager-info {
  color: var(--muted);
  font-size: var(--fs-sm);
}
</style>
