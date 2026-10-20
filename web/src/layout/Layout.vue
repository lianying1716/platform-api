<template>
  <div class="ui-app" :class="{ 'demo-mode': DEMO_MODE }">
    <aside class="ui-side">
      <div class="ui-brand">
        <img class="mark" src="/logo-symbol-dark.svg" alt="星采 Stargather" width="30" height="30" />
        <span class="bt">星采</span>
        <span class="bt-en">Stargather</span>
      </div>
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
      <nav class="ui-mobile-nav" aria-label="主导航">
        <router-link v-for="item in menu" :key="item.path" :to="item.path" class="ui-mobile-link">
          {{ item.title }}
        </router-link>
      </nav>
      <header class="ui-top">
        <span class="ui-crumb"><b>{{ route.meta.title }}</b></span>
        <span v-if="DEMO_MODE" class="ui-badge acc">演示数据 · 只读展示</span>
        <div class="grow" />
        <router-link class="ui-btn sm" to="/assets">素材库</router-link>
        <router-link class="ui-btn sm" to="/system">系统设置</router-link>
      </header>
      <main class="ui-content">
        <!--
          对标记了 meta.keepAlive 的页面做缓存：切页返回时复用组件实例（数据保留），
          不再每次 onMounted 重新拉全量——总览/素材库这种重数据页尤其明显。
          需即时性的页面（处理队列有 5s 轮询）不缓存。
        -->
        <router-view v-slot="{ Component }">
          <keep-alive :include="keepAliveNames">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { DEMO_MODE } from '../demo/runtime'
import '../styles/ui.css'

const route = useRoute()
const router = useRouter()
let eventSource: EventSource | null = null
let reconnectTimer: number | undefined
let lastEventId = ''
let fallbackTimer: number | undefined

// The browser's EventSource cannot set Authorization headers, so SSE relies on
// the httpOnly session cookie issued at login. Last-Event-ID lets the server
// replay outbox rows that arrived while the connection was down.
function connectEvents() {
  if (DEMO_MODE || typeof EventSource === 'undefined' || !localStorage.getItem('token')) return
  eventSource?.close()
  // EventSource retries natively and sends Last-Event-ID. Keep the cursor in
  // the URL too so a page reload can replay missed events explicitly.
  const url = lastEventId
    ? `/api/events/stream?last_event_id=${encodeURIComponent(lastEventId)}`
    : '/api/events/stream'
  eventSource = new EventSource(url)
  eventSource.onopen = () => { window.clearTimeout(reconnectTimer) }
  eventSource.onmessage = (event) => {
    if (event.lastEventId) lastEventId = event.lastEventId
  }
  for (const name of ['asset_status', 'sync_done', 'crawl_done', 'pipeline_status', 'breakdown_status', 'job_alert']) {
    eventSource.addEventListener(name, (event) => {
      const id = (event as MessageEvent).lastEventId
      if (id) lastEventId = id
      refreshCurrentPage()
    })
  }
  eventSource.onerror = () => {
    // Do not reconnect on the initial 401; login will mount this layout again.
    if (!localStorage.getItem('token')) return
    eventSource?.close()
    eventSource = null
    window.clearTimeout(reconnectTimer)
    reconnectTimer = window.setTimeout(connectEvents, 2000)
  }
}

// Pages own their data loading. A lightweight custom event lets the active
// keep-alive page refresh incrementally without coupling Layout to every page.
function refreshCurrentPage() {
  window.dispatchEvent(new CustomEvent('xsai:events-changed'))
}

// keep-alive 的 include 用组件名匹配；只缓存显式声明 meta.keepAlive 的页面。
// 注意：router.getRoutes() 返回的是**扁平化**后的路由（子路由 path 已拼成完整路径），
// 不能再走 .children，否则 include 恒为空、keep-alive 形同虚设。
const keepAliveNames = computed(
  () =>
    router
      .getRoutes()
      .filter((r) => r.meta?.keepAlive)
      .map((r) => (r.components?.default as any)?.name)
      .filter(Boolean) as string[],
)

// 导航顺序与信息架构同步：总览（合并数据看板）、关注列表（合并订阅同步）
const menu = [
  { path: '/overview', title: '总览', short: '◧' },
  { path: '/accounts', title: '账号中心', short: '◆' },
  { path: '/crawl', title: '关键词采集', short: '⌕' },
  { path: '/followed-creators', title: '关注列表', short: '★' },
  { path: '/assets', title: '素材库', short: '▤' },
  { path: '/pipeline', title: '处理队列', short: '≡' },
  { path: '/models', title: '模型接入', short: '◈' },
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
  eventSource?.close()
  eventSource = null
  localStorage.removeItem('token')
  // 清掉服务端会话 Cookie，避免 <video> 等标签仍能访问受保护资源
  api.post('/api/auth/logout').catch(() => {})
  router.push('/login')
}

onMounted(connectEvents)
onUnmounted(() => {
  eventSource?.close()
  eventSource = null
  window.clearTimeout(reconnectTimer)
  window.clearInterval(fallbackTimer)
})
</script>

<style scoped>
.ui-main, .ui-content { min-width: 0; }
.ui-mobile-nav { display: none; }
@media (max-width: 820px) {
  .ui-app { grid-template-columns: 1fr; }
  .ui-app .ui-side { display: none; }
  .ui-mobile-nav {
    display: flex; align-items: center; gap: 4px; overflow-x: auto;
    padding: 8px 12px; margin: 0; background: var(--surface);
    border-bottom: 1px solid var(--line); white-space: nowrap;
  }
  .ui-mobile-link {
    flex: none; padding: 5px 9px; color: var(--ink-2); text-decoration: none;
    font-size: var(--fs-sm); border: 1px solid var(--line); border-radius: 4px;
  }
  .ui-mobile-link.router-link-active { color: var(--accent); border-color: var(--accent); }
  .ui-content { padding: 14px 12px; }
  .ui-top { height: auto; flex-wrap: wrap; padding: 8px 12px; }
}
.ui-brand .bt { white-space: nowrap; font-size: 16px; }
.ui-brand .bt-en {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--sidebar-ink-dim);
  align-self: flex-end;
  padding-bottom: 1px;
}
/* 品牌标志（M3）：深色侧栏用透明底版本，无白色方底 */
.ui-brand .mark { background: none; width: 30px; height: 30px; flex: none; display: block; }

/* V3：左侧栏顶部加一条砖红色块，呼应包豪斯原色。
   用绝对定位，不参与 flex 流，避免把侧栏内容顶出视口。 */
.ui-side { position: sticky; box-sizing: border-box; }
.ui-side::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 6px;
  background: var(--accent);
}

/* 侧栏内边距留出顶部色块的位置 */
.ui-side { padding-top: calc(var(--sp-5) + 6px); }

/* 导航可滚动，底部操作区始终贴底可见 */
.ui-nav { overflow-y: auto; min-height: 0; }

.ui-nav-item { text-decoration: none; }
.ui-nav-item .ic {
  width: 20px;
  text-align: center;
  font-size: 15px;
  opacity: 0.85;
}
.ui-nav-item.on .ic { opacity: 1; }

.ui-side-foot { display: flex; flex-direction: column; gap: 8px; }
.ui-user { color: var(--sidebar-ink-dim); }
.logout { justify-content: flex-start; color: var(--sidebar-ink-dim); }
.logout:hover { color: var(--sidebar-ink); background: color-mix(in oklab, var(--sidebar-ink) 10%, transparent); }
</style>
