<template>
  <div class="login-wrap">
    <!-- 左：品牌 + 价值主张（讲清产品是什么、解决什么烦恼） -->
    <section class="brand-side">
      <div class="brand-head">
        <div class="brand-tile">
          <img src="/logo-symbol.svg" alt="星采 Stargather" width="52" height="52" />
        </div>
        <div class="brand-name">
          <b>星采</b><span>Stargather</span>
        </div>
      </div>

      <h1 class="brand-h1">把收藏夹，<br />变成用得上<em>的个人素材库</em></h1>
      <p class="brand-lead">
        在 B站 / 抖音 / 小红书 收藏了却没空回看的视频，自动归集、下载、转成可搜索文字，
        让每次收藏都真正用得上。
      </p>

      <ul class="brand-points">
        <li><span class="pt-n">01</span><span class="pt-t">多平台自动归集<em>收藏、关注一处看全</em></span></li>
        <li><span class="pt-n">02</span><span class="pt-t">下载即转文字<em>本地转写，可搜索、可引用</em></span></li>
        <li><span class="pt-n">03</span><span class="pt-t">AI 判类与拆解<em>看懂为什么火，怎么复用</em></span></li>
      </ul>

      <img class="brand-illus" src="/brand/login-illustration.png" alt="" />
    </section>

    <!-- 右：登录卡 -->
    <section class="form-side">
      <div class="login-card">
        <h2>{{ initialized === false ? '首次设置' : '登录' }}</h2>
        <p class="card-sub" v-if="initialized === false">设置管理员密码（至少 6 位），仅本机使用</p>
        <p class="card-sub" v-else>输入管理员密码进入工作台</p>

        <form @submit.prevent="submit">
          <label class="fld">
            <span class="fld-k">{{ initialized === false ? '新密码' : '管理员密码' }}</span>
            <input
              v-model="password" type="password" :placeholder="initialized === false ? '至少 6 位' : '请输入密码'"
              autocomplete="current-password" @keyup.enter="submit"
            />
          </label>
          <label class="fld" v-if="initialized === false">
            <span class="fld-k">确认密码</span>
            <input
              v-model="confirm" type="password" placeholder="再输入一次"
              autocomplete="new-password" @keyup.enter="submit"
            />
          </label>

          <button class="fld-btn" type="submit" :disabled="loading">
            {{ loading ? '处理中…' : (initialized === false ? '设置密码并进入' : '进入工作台') }}
          </button>
        </form>
      </div>

      <p class="form-foot">单管理员 · 本地部署 · 凭证 AES-GCM 加密存储</p>
      <p class="form-foot dim">首次使用会引导你设置密码</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiCall } from '../api'

const router = useRouter()
const initialized = ref<boolean | null>(null)
const password = ref('')
const confirm = ref('')
const loading = ref(false)

onMounted(async () => {
  const data = await apiCall('auth_status_api_auth_status_get')
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
    const data = initialized.value === false
      ? await apiCall('setup_api_auth_setup_post', {}, { body: { password: password.value } })
      : await apiCall('login_api_auth_login_post', {}, { body: { password: password.value } })
    localStorage.setItem('token', data.token)
    router.push('/overview')
  } catch {
    // The shared axios interceptor already presents the actionable message
    // (401/429/network). Keep the rejection handled so the browser console does
    // not report an unhandled promise while the user remains on this form.
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 1.15fr 0.85fr;
  background: var(--bg);
  border-top: 6px solid var(--ink);
}

/* ---------- 左：品牌 + 主张 ---------- */
.brand-side {
  box-sizing: border-box;
  position: relative;
  padding: 44px 56px 36px;
  display: flex;
  flex-direction: column;
  border-right: 2px solid var(--ink);
  /* 锁定一屏高度，内部用 flex 分配；插画只吃剩余空间，不把内容顶出视口 */
  height: 100dvh;
  overflow: hidden;
}
.brand-head { display: flex; align-items: center; gap: 12px; }
.brand-tile {
  width: 60px; height: 60px; flex: none;
  display: grid; place-items: center;
  background: var(--bg);
  border: 2px solid var(--ink);
  box-shadow: var(--shadow-1);
}
.brand-name { display: flex; align-items: baseline; gap: 8px; }
.brand-name b { font-size: 22px; letter-spacing: -0.01em; }
.brand-name span {
  font-size: 11px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase;
  color: var(--muted);
}

.brand-h1 {
  margin: 34px 0 0;
  font-size: 38px;
  line-height: 1.28;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--ink);
}
.brand-h1 em { font-style: normal; color: var(--accent); }

.brand-lead {
  margin: 16px 0 0;
  max-width: 46ch;
  font-size: 15px;
  line-height: 1.75;
  color: var(--ink-2);
}

.brand-points { list-style: none; margin: 22px 0 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.brand-points li { display: flex; gap: 12px; align-items: flex-start; }
.pt-n {
  flex: none; font-family: var(--font-mono); font-size: 12px; font-weight: 700;
  color: var(--accent); padding-top: 2px; letter-spacing: .04em;
}
.pt-t { display: flex; flex-direction: column; gap: 1px; font-size: 15px; font-weight: 650; color: var(--ink); }
.pt-t em { font-style: normal; font-size: 13px; font-weight: 400; color: var(--muted); }

/* 插画：作为文档流里的收尾元素，靠底对齐；不与上方文字重叠 */
.brand-illus {
  margin-top: 18px;
  width: 100%;
  max-height: 100%;
  /* flex 收缩：剩余多少空间就用多少，保证整张图在视口内 */
  flex: 1 1 auto;
  min-height: 0;
  align-self: center;
  object-fit: contain;
  object-position: center bottom;
  pointer-events: none;
  user-select: none;
}

/* ---------- 右：登录卡 ---------- */
.form-side {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 18px;
  padding: 40px 32px;
  background:
    repeating-linear-gradient(45deg, transparent 0 26px, color-mix(in oklab, var(--ink) 2%, transparent) 26px 27px);
}
.login-card {
  width: min(400px, 100%);
  padding: 32px 30px 28px;
  background: var(--surface);
  border: 2px solid var(--ink);
  box-shadow: var(--shadow-2);
}
.login-card h2 { margin: 0 0 4px; font-size: 22px; letter-spacing: -0.01em; }
.card-sub { margin: 0 0 20px; color: var(--muted); font-size: var(--fs-sm); }

.fld { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
.fld-k { font-size: var(--fs-sm); color: var(--ink-2); font-weight: 600; }
.fld input {
  font-family: inherit; font-size: var(--fs-base);
  padding: 10px 12px;
  background: var(--surface-2); color: var(--ink);
  border: 1px solid var(--ink);
  border-radius: 0;
  outline: none;
  transition: box-shadow .12s ease-out, background .12s ease-out;
}
.fld input:focus { background: var(--surface); box-shadow: 2px 2px 0 var(--ink); }

.fld-btn {
  width: 100%; margin-top: 4px;
  font-family: inherit; font-size: var(--fs-base); font-weight: 700;
  padding: 11px 14px; cursor: pointer;
  background: var(--accent); color: var(--accent-ink);
  border: 2px solid var(--ink); border-radius: 0;
  box-shadow: var(--shadow-1);
  transition: transform .12s ease-out, box-shadow .12s ease-out;
}
.fld-btn:hover:not(:disabled) { transform: translate(-1px, -1px); box-shadow: var(--shadow-2); }
.fld-btn:active:not(:disabled) { transform: translate(1px, 1px); box-shadow: none; }
.fld-btn:disabled { opacity: .6; cursor: default; }

.form-foot { margin: 0; font-size: var(--fs-xs); color: var(--muted); letter-spacing: .02em; }
.form-foot.dim { opacity: .7; }

/* 窄屏：单列堆叠，插画与长文案收起，登录卡优先 */
@media (max-width: 900px) {
  .login-wrap { grid-template-columns: 1fr; }
  /* 窄屏无需撑满一屏，避免把登录框顶到首屏之外 */
  .brand-side {
    min-height: 0;
    padding: 28px 22px 22px;
    border-right: 0;
    border-bottom: 2px solid var(--ink);
  }
  .brand-h1 { margin-top: 20px; font-size: 26px; }
  .brand-lead { font-size: 14px; margin-top: 12px; }
  .brand-points { margin-top: 18px; }
  .brand-illus { display: none; }
  /* 登录卡紧随其后，窄屏也一眼可见 */
  .form-side { padding: 24px 20px 40px; justify-content: flex-start; }
  .login-card { width: min(420px, 100%); }
}
</style>
