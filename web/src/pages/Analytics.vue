<template>
  <div>
    <el-tabs v-model="tab">
      <el-tab-pane label="作者维度" name="creators">
        <el-table :data="creators" v-loading="loading" stripe>
          <el-table-column prop="author_name" label="作者" min-width="160">
            <template #default="{ row }">{{ row.author_name || row.author_id }}</template>
          </el-table-column>
          <el-table-column prop="platform" label="平台" width="120" />
          <el-table-column prop="content_count" label="内容数" width="120" sortable />
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
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api } from '../api'

const tab = ref('creators')
const creators = ref<any[]>([])
const keywords = ref<any[]>([])
const loading = ref(false)

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
</style>
