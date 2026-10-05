<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2>素材采集工作台</h2>
      <p class="sub" v-if="initialized === false">首次使用，请设置管理员密码（至少 6 位）</p>
      <p class="sub" v-else>请输入管理员密码登录</p>
      <el-form @submit.prevent="submit">
        <el-form-item>
          <el-input v-model="password" type="password" show-password placeholder="管理员密码"
            @keyup.enter="submit" />
        </el-form-item>
        <el-form-item v-if="initialized === false">
          <el-input v-model="confirm" type="password" show-password placeholder="再输入一次确认"
            @keyup.enter="submit" />
        </el-form-item>
        <el-button type="primary" style="width: 100%" :loading="loading" @click="submit">
          {{ initialized === false ? '设置密码并进入' : '登录' }}
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const initialized = ref<boolean | null>(null)
const password = ref('')
const confirm = ref('')
const loading = ref(false)

onMounted(async () => {
  const { data } = await api.get('/api/auth/status')
  initialized.value = data.initialized
})

async function submit() {
  if (!password.value) return
  if (initialized.value === false && password.value !== confirm.value) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  loading.value = true
  try {
    const url = initialized.value === false ? '/api/auth/setup' : '/api/auth/login'
    const { data } = await api.post(url, { password: password.value })
    localStorage.setItem('token', data.token)
    router.push('/accounts')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #1d2129 0%, #2b3245 100%);
}

.login-card {
  width: 360px;
  padding: 8px 12px;
}

h2 {
  margin: 4px 0 8px;
}

.sub {
  color: #909399;
  font-size: 13px;
  margin-bottom: 18px;
}
</style>
