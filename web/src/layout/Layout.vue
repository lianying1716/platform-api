<template>
  <div class="ui-app">
    <aside class="ui-side">
      <div class="ui-brand"><span class="mark">素</span><span class="bt">素材采集工作台</span></div>
      <nav class="ui-nav">
        <router-link
          v-for="item in menu"
          :key="item.path"
          :to="item.path"
          class="ui-nav-item"
          :class="{ on: isActive(item.path) }"
        >
          <span aria-hidden="true" class="ic">{{ item.short }}</span>
          <span class="label">{{ item.title }}</span>
        </router-link>
      </nav>
      <div class="ui-side-foot">
        <div class="ui-user">
          <span class="ui-dot ok" />
          <span>admin · 本机</span>
        </div>
        <button class="ui-btn ghost sm logout" @click="logout">退出登录</button>
      </div>
    </aside>

    <div class="ui-main">
      <header class="ui-top">
        <span class="ui-crumb"><b>{{ route.meta.title }}</b></span>
        <div class="grow" />
        <router-link class="ui-btn sm" to="/assets">素材库</router-link>
        <router-link class="ui-btn sm" to="/system">系统设置</router-link>
      </header>
      <main class="ui-content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import '../styles/ui.css'

const route = useRoute()
const router = useRouter()

// 导航顺序与信息架构同步：总览（合并数据看板）、关注列表（合并订阅同步）
const menu = [
  { path: '/overview', title: '总览', short: '◧' },
  { path: '/accounts', title: '账号中心', short: '◆' },
  { path: '/crawl', title: '关键词采集', short: '⌕' },
  { path: '/followed-creators', title: '关注列表', short: '★' },
  { path: '/assets', title: '素材库', short: '▤' },
  { path: '/pipeline', title: '处理队列', short: '≡' },
  { path: '/ai', title: 'AI 接入', short: '⌘' },
  { path: '/system', title: '系统设置', short: '⚙' },
]

// /analytics 已合并进 /overview，/subscriptions 已合并进 /followed-creators
function isActive(path: string) {
  if (path === '/overview') return route.path === '/overview' || route.path === '/analytics'
  if (path === '/followed-creators') {
    return route.path === '/followed-creators' || route.path === '/subscriptions'
  }
  return route.path === path
}

function logout() {
  localStorage.removeItem('token')
  // 清掉服务端会话 Cookie，避免 <video> 等标签仍能访问受保护资源
  api.post('/api/auth/logout').catch(() => {})
  router.push('/login')
}
</script>

<style scoped>
.ui-brand .bt { white-space: nowrap; }

/* V3：左侧栏顶部加一条砖红色块，呼应包豪斯原色 */
.ui-side::before {
  content: '';
  display: block;
  height: 6px;
  margin: calc(-1 * var(--sp-5)) 0 var(--sp-5);
  background: var(--accent);
}

.ui-nav-item { text-decoration: none; }
.ui-nav-item .ic {
  width: 18px;
  text-align: center;
  font-size: 13px;
  opacity: 0.85;
}
.ui-nav-item.on .ic { opacity: 1; }

.ui-side-foot { display: flex; flex-direction: column; gap: 8px; }
.ui-user { color: var(--sidebar-ink-dim); }
.logout { justify-content: flex-start; color: var(--sidebar-ink-dim); }
.logout:hover { color: var(--sidebar-ink); background: color-mix(in oklab, var(--sidebar-ink) 10%, transparent); }
</style>
