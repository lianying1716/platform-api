<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建订阅</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table :data="subs" v-loading="loading" style="margin-top: 12px">
      <el-table-column prop="platform" label="平台" width="90" />
      <el-table-column prop="title" label="来源" min-width="160">
        <template #default="{ row }">{{ row.title || row.remote_id }}</template>
      </el-table-column>
      <el-table-column label="类型" width="110">
        <template #default="{ row }">{{ sourceText(row.source_type) }}</template>
      </el-table-column>
      <el-table-column label="自动下载" width="90">
        <template #default="{ row }"><el-tag size="small" :type="row.auto_download ? 'success' : 'info'">{{ row.auto_download ? '是' : '否' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="周期" width="100">
        <template #default="{ row }">{{ row.interval_minutes }} 分钟</template>
      </el-table-column>
      <el-table-column label="上次同步" width="170">
        <template #default="{ row }">{{ fmt(row.last_sync_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :loading="syncing === row.id" @click="sync(row)">立即同步</el-button>
          <el-switch :model-value="row.enabled" size="small" style="margin: 0 8px" @change="toggle(row)" />
          <el-popconfirm title="删除该订阅？" @confirm="remove(row)">
            <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" title="新建订阅" width="480px">
      <el-form label-width="90px">
        <el-form-item label="账号">
          <el-select v-model="form.account_id" placeholder="选择已登录账号" style="width: 100%">
            <el-option v-for="a in accounts" :key="a.id" :label="`${a.platform_label} - ${a.display_name || a.id}`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源类型">
          <el-select v-model="form.source_type" style="width: 100%">
            <el-option label="收藏夹" value="favorite" />
            <el-option label="关注博主" value="creator" />
            <el-option label="合集" value="collection" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源ID"><el-input v-model="form.remote_id" placeholder="收藏夹ID / UP主mid / 博主sec_uid" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="自动下载"><el-switch v-model="form.auto_download" /></el-form-item>
        <el-form-item label="周期(分钟)"><el-input-number v-model="form.interval_minutes" :min="10" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const subs = ref<any[]>([])
const accounts = ref<any[]>([])
const loading = ref(false)
const syncing = ref<number | null>(null)
const dialog = ref(false)
const form = reactive({ account_id: null, source_type: 'favorite', remote_id: '', title: '', auto_download: false, interval_minutes: 360 })

function sourceText(v: string) { return ({ favorite: '收藏夹', creator: '关注博主', collection: '合集', like: '点赞' } as Record<string, string>)[v] ?? v }
function fmt(v: string | null) { return v ? new Date(v + 'Z').toLocaleString('zh-CN', { hour12: false }) : '从未' }

async function load() {
  loading.value = true
  try {
    subs.value = (await api.get('/api/subscriptions')).data
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, { account_id: null, source_type: 'favorite', remote_id: '', title: '', auto_download: false, interval_minutes: 360 })
  dialog.value = true
}

async function submit() {
  await api.post('/api/subscriptions', form)
  ElMessage.success('已创建')
  dialog.value = false
  await load()
}

async function sync(row: any) {
  syncing.value = row.id
  try {
    const { data } = await api.post(`/api/subscriptions/${row.id}/sync`)
    ElMessage({ message: data.status === 'success' ? `同步完成，新增 ${data.added}` : data.error, type: data.status === 'success' ? 'success' : 'warning' })
    await load()
  } finally {
    syncing.value = null
  }
}

async function toggle(row: any) {
  await api.put(`/api/subscriptions/${row.id}`, { enabled: !row.enabled })
  await load()
}

async function remove(row: any) {
  await api.delete(`/api/subscriptions/${row.id}`)
  ElMessage.success('已删除')
  await load()
}

onMounted(async () => {
  accounts.value = (await api.get('/api/accounts')).data
  await load()
})
</script>

<style scoped>
.toolbar { display: flex; gap: 8px; }
</style>
