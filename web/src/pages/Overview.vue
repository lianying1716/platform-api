<template>
  <div v-loading="loading">
    <el-row :gutter="16">
      <el-col :span="6" v-for="card in cards" :key="card.key">
        <el-card shadow="hover" class="stat" :class="card.alert ? 'alert' : ''" @click="go(card.to)">
          <div class="num">{{ card.value }}</div>
          <div class="label">{{ card.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card style="margin-top: 16px" header="最近 24 小时热门素材 Top 10">
      <el-table :data="hot" size="small">
        <el-table-column type="index" width="50" />
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="platform" label="平台" width="90" />
        <el-table-column prop="author_name" label="作者" width="120" />
        <el-table-column prop="play" label="播放" width="100" />
        <el-table-column prop="like" label="点赞" width="100" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const loading = ref(false)
const hot = ref<any[]>([])
const cards = ref<{ key: string; label: string; value: number; to: string; alert?: boolean }[]>([])

function go(to: string) {
  router.push(to)
}

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await api.get('/api/dashboard/summary')
    cards.value = [
      { key: 'acc', label: '账号异常', value: data.accounts_error, to: '/accounts', alert: data.accounts_error > 0 },
      { key: 'today', label: '今日抓取', value: data.crawled_today, to: '/assets' },
      { key: 'pending', label: '待处理任务', value: data.jobs_pending, to: '/pipeline' },
      { key: 'failed', label: '失败任务', value: data.jobs_failed, to: '/pipeline', alert: data.jobs_failed > 0 },
    ]
    hot.value = data.hot_assets || []
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.stat {
  cursor: pointer;
  text-align: center;
}
.stat.alert {
  border-color: #f56c6c;
}
.num {
  font-size: 30px;
  font-weight: 700;
}
.label {
  color: #909399;
  margin-top: 6px;
}
</style>
