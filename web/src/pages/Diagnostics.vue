<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>依赖与环境诊断</span>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
    </template>
    <el-table :data="checks" v-loading="loading">
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.ok ? 'success' : 'danger'" effect="dark" size="small">
            {{ row.ok ? '正常' : '异常' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="项目" width="160" />
      <el-table-column prop="detail" label="说明" />
    </el-table>
  </el-card>
  <el-card style="margin-top: 16px">
    <template #header>修改管理员密码</template>
    <el-form inline>
      <el-form-item label="原密码">
        <el-input v-model="oldPwd" type="password" show-password style="width: 180px" />
      </el-form-item>
      <el-form-item label="新密码">
        <el-input v-model="newPwd" type="password" show-password style="width: 180px" />
      </el-form-item>
      <el-button type="primary" @click="changePwd">修改</el-button>
    </el-form>
  </el-card>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { api } from '../api'

const checks = ref<{ name: string; ok: boolean; detail: string }[]>([])
const loading = ref(false)
const oldPwd = ref('')
const newPwd = ref('')

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/api/system/diagnostics')
    checks.value = data.checks
  } finally {
    loading.value = false
  }
}

async function changePwd() {
  await api.post('/api/auth/change-password', { old_password: oldPwd.value, new_password: newPwd.value })
  ElMessage.success('密码已修改')
  oldPwd.value = newPwd.value = ''
}

onMounted(load)
</script>
