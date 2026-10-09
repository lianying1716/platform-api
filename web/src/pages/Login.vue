<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <div class="brand-tile">
        <img class="brand-mark" src="/logo-symbol.svg" alt="星采 Stargather" width="52" height="52" />
      </div>
      <h2>星采 <span class="en">Stargather</span></h2>
      <p class="tagline">自媒体创作的自动化素材管理加工</p>
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
  /* V3 包豪斯：米白底 + 顶部粗黑边，不用渐变 */
  background: var(--bg);
  border-top: 6px solid var(--ink);
}

.login-card {
  width: 360px;
  padding: 8px 12px;
  border: 2px solid var(--ink);
  border-radius: 0;
  box-shadow: var(--shadow-2);
}

h2 {
  margin: 4px 0 8px;
  display: flex;
  align-items: baseline;
  gap: 8px;
  letter-spacing: -0.01em;
}
h2 .en {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
}

.brand-tile {
  width: 68px;
  height: 68px;
  margin: 2px auto 14px;
  display: grid;
  place-items: center;
  background: var(--bg);
  border: 2px solid var(--ink);
  box-shadow: var(--shadow-1);
}
.brand-mark {
  display: block;
  width: 52px;
  height: 52px;
}

.tagline {
  margin: 0 0 14px;
  color: var(--ink-2);
  font-size: var(--fs-sm);
}

.login-card :deep(.el-input__wrapper) {
  border-radius: 0;
  box-shadow: 0 0 0 1px var(--ink) inset;
}

.login-card :deep(.el-button--primary) {
  border-radius: 0;
  box-shadow: var(--shadow-1);
}

.sub {
  color: var(--muted);
  font-size: var(--fs-sm);
  margin-bottom: 18px;
}
</style>
